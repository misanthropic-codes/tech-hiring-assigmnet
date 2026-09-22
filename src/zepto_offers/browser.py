from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Browser, Page, Playwright, sync_playwright


FETCH_LIST_SUBSTRING = "cfs/api/v1/cart/coupons/fetch-list"
COUPON_URL_HINTS = (
    "cfs/api/v1/cart/coupons",
    "coupon-service/",
    "payment-service/api/payment-listing",
    "fetch-list",
    "payment-offer",
    "payment_offer",
    "bank-offer",
    "bank_offer",
)


@dataclass
class BrowserResult:
    payload: Any | None = None
    matched_url: str | None = None
    store_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    notes: list[str] = field(default_factory=list)
    storage_state_path: str | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cookie_map(cookies: list[dict[str, Any]]) -> dict[str, str]:
    return {str(c.get("name", "")): str(c.get("value", "")) for c in cookies}


def _is_authenticated_from_cookies(cookies: list[dict[str, Any]]) -> bool:
    """True when Zepto session cookies indicate an already-logged-in user."""
    by_name = _cookie_map(cookies)
    lower = {k.lower(): v for k, v in by_name.items()}

    access = lower.get("accesstoken") or lower.get("access_token") or ""
    refresh = lower.get("refreshtoken") or lower.get("refresh_token") or ""
    user_id = lower.get("user_id") or lower.get("userid") or lower.get("uid") or ""
    is_auth = (by_name.get("isAuth") or lower.get("isauth") or "").strip().lower()

    # Explicit logout / guest flag wins even if stale tokens linger
    if is_auth in {"false", "0", "no"}:
        return False
    if is_auth in {"true", "1", "yes"} and (access or user_id):
        return True
    if access and len(access) > 20 and user_id:
        return True
    if access and refresh and len(access) > 20:
        return True
    return False


class OfferBrowser:
    def __init__(
        self,
        *,
        lat: float = 12.96902,
        lng: float = 77.75395,
        headed: bool | None = None,
        storage_state: str | Path | None = None,
        timeout_ms: int = 90_000,
        phone: str | None = None,
    ) -> None:
        self.lat = lat
        self.lng = lng
        self.headed = _env_bool("ZEPTO_HEADED", True) if headed is None else headed
        self.storage_state = Path(storage_state) if storage_state else None
        self.timeout_ms = timeout_ms
        self.phone = phone
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "OfferBrowser":
        self._playwright = sync_playwright().start()
        launch_args = ["--disable-dev-shm-usage", "--no-sandbox"]
        last_err: Exception | None = None
        for kwargs in (
            {"channel": "chrome"},
            {"executable_path": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"},
            {},
        ):
            try:
                self._browser = self._playwright.chromium.launch(
                    headless=not self.headed,
                    args=launch_args,
                    **kwargs,
                )
                return self
            except Exception as exc:
                last_err = exc
                continue
        raise RuntimeError(f"Could not launch Chrome/Chromium: {last_err}") from last_err

    def __exit__(self, *exc: object) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _new_page(self) -> tuple[Any, Page]:
        assert self._browser is not None
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
            "geolocation": {"latitude": self.lat, "longitude": self.lng},
            "permissions": ["geolocation"],
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if self.storage_state and self.storage_state.exists():
            context_kwargs["storage_state"] = str(self.storage_state)
        context = self._browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(self.timeout_ms)
        return context, page

    def _auth_cookie_status(self, context: Any) -> tuple[bool, str]:
        try:
            cookies = context.cookies()
        except Exception as exc:
            return False, f"cookie read failed: {exc}"
        if _is_authenticated_from_cookies(cookies):
            by_name = _cookie_map(cookies)
            uid = by_name.get("user_id") or by_name.get("userId") or "?"
            return True, f"auth cookies present (user_id={uid[:8]}…)"
        return False, "no auth cookies"

    def save_login_state(self, path: str | Path, *, phone: str | None = None) -> Path:
        """Open Zepto headed so the user can complete OTP login, then persist storage state."""
        path = Path(path)
        context, page = self._new_page()
        page.goto("https://www.zepto.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        already, detail = self._auth_cookie_status(context)
        if already:
            print(f"Already logged in ({detail}). Saving session without OTP.", flush=True)
            context.storage_state(path=str(path))
            context.close()
            return path

        notes = [
            "Complete login in the opened browser (phone + OTP), then return here.",
            "Waiting up to 5 minutes for an authenticated cookie/session…",
        ]
        if phone:
            notes.insert(1, f"Phone prefilled: {phone} — enter OTP when prompted.")
        print("\n".join(notes), flush=True)

        # Click Login if visible
        try:
            page.get_by_role("button", name=re.compile(r"login", re.I)).first.click(timeout=5000)
        except Exception:
            try:
                page.get_by_text(re.compile(r"^login$", re.I)).first.click(timeout=3000)
            except Exception:
                pass

        if phone:
            self._fill_phone_and_continue(page, phone)

        deadline = time.time() + 300
        while time.time() < deadline:
            ok, _ = self._auth_cookie_status(context)
            if ok:
                print("Login detected via cookies.", flush=True)
                break
            page.wait_for_timeout(2000)

        context.storage_state(path=str(path))
        context.close()
        return path

    def _fill_phone_and_continue(self, page: Page, phone: str) -> None:
        """Fill mobile number on Zepto login and click Continue / Get OTP if present."""
        digits = re.sub(r"\D", "", phone)
        if digits.startswith("91") and len(digits) > 10:
            digits = digits[-10:]
        try:
            # Modal can take a moment after Login click / soft navigation
            page.wait_for_timeout(2000)
            phone_input = page.locator(
                'input[placeholder*="Enter Phone Number" i], '
                'input[placeholder*="phone number" i], '
                'input[placeholder*="Phone Number" i], '
                'input[type="tel"], input[inputmode="numeric"], '
                'input[placeholder*="mobile" i], input[placeholder*="phone" i], '
                'input[name*="phone" i], input[name*="mobile" i]'
            ).first
            phone_input.wait_for(state="visible", timeout=25_000)
            phone_input.click()
            phone_input.fill("")
            phone_input.type(digits, delay=50)
            page.wait_for_timeout(800)
            for label in ("Continue", "Get OTP", "Send OTP", "Proceed"):
                btn = page.get_by_role("button", name=re.compile(rf"^{label}$", re.I))
                if btn.count() and btn.first.is_visible():
                    # Continue may stay disabled until 10 digits are accepted
                    try:
                        btn.first.click(timeout=5000)
                    except Exception:
                        page.keyboard.press("Enter")
                    print("Submitted phone; enter OTP in the browser window.", flush=True)
                    return
            print("Phone filled; click Continue if needed, then enter OTP.", flush=True)
        except Exception as exc:
            print(
                f"Could not auto-fill phone ({exc}). "
                f"Enter {digits} manually in the login modal, then OTP.",
                flush=True,
            )

    def fetch_coupon_payload(self, *, wait_for_login: bool = False) -> BrowserResult:
        context, page = self._new_page()
        result = BrowserResult(lat=self.lat, lng=self.lng)
        payloads: list[tuple[str, Any]] = []

        def on_response(resp: Any) -> None:
            url = resp.url
            if not any(h in url for h in COUPON_URL_HINTS):
                return
            try:
                data = resp.json()
            except Exception:
                return
            payloads.append((url, data))
            if FETCH_LIST_SUBSTRING in url:
                # Prefer payloads that include bank/card offer types when multiple fire
                prefer = False
                try:
                    blob = json.dumps(data).upper()
                    prefer = "BANK_OFFER" in blob or "CARD_OFFER" in blob
                except Exception:
                    prefer = False
                if result.payload is None or prefer:
                    result.payload = data
                    result.matched_url = url

        def on_request(req: Any) -> None:
            if "lms/api/v2/get_page" in req.url:
                # Capture store headers if present later via response handler
                pass

        page.on("response", on_response)
        page.on("request", on_request)

        try:
            page.goto("https://www.zepto.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            cookie_ok, cookie_detail = self._auth_cookie_status(context)
            if cookie_ok:
                result.notes.append(f"Session already authenticated ({cookie_detail}).")
                print(f"Already logged in via cookies — skipping OTP ({cookie_detail}).", flush=True)
            else:
                result.notes.append(f"Not authenticated yet ({cookie_detail}).")

            result.store_id = self._capture_store_id(page)
            self._ensure_location(page, result)
            self._add_first_item(page, result)
            self._open_cart(page, result)

            # Re-check cookies after navigation (storage_state / soft login may apply late)
            cookie_ok, cookie_detail = self._auth_cookie_status(context)
            ui_ok = self._coupons_accessible(page)
            logged_in = cookie_ok or ui_ok
            if cookie_ok and not ui_ok:
                result.notes.append(
                    "Auth cookies present but coupons UI still gated; refreshing and probing."
                )
                try:
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                    self._open_cart(page, result)
                except Exception as exc:
                    result.notes.append(f"Reload after cookie-auth skipped: {exc}")
                self._open_coupons_panel(page, result)
                ui_ok = self._coupons_accessible(page)
                logged_in = cookie_ok or ui_ok

            if not logged_in:
                # Storage-state sessions sometimes still show a transient login CTA;
                # probe coupons once before forcing interactive wait.
                self._open_coupons_panel(page, result)
                logged_in = (
                    self._auth_cookie_status(context)[0]
                    or self._coupons_accessible(page)
                    or bool(
                        page.get_by_text(
                            re.compile(r"payment offers|view coupons|apply coupon", re.I)
                        ).count()
                    )
                )
            if not logged_in:
                result.notes.append(
                    "Coupons are login-gated (UI shows 'Login to view coupons')."
                )
                if wait_for_login or self.phone:
                    result.notes.append("Waiting for interactive login (up to 5 minutes)…")
                    print(
                        "Please log in in the browser window (OTP). Waiting for Payment Offers…",
                        flush=True,
                    )
                    self._click_login(page)
                    self._wait_until_coupons_or_timeout(
                        page, result, seconds=300, phone=self.phone
                    )
                else:
                    result.notes.append(
                        "Re-run with --login / --wait-login to save a session, or pass --phone."
                    )
            else:
                result.notes.append("Proceeding with authenticated session (cookies and/or coupons UI).")

            self._open_coupons_panel(page, result)
            self._ensure_payment_offers_and_capture(page, result, payloads)

            if payloads:
                # Always prefer fetch-list payloads that include BANK_OFFER
                bankish: list[tuple[str, Any]] = []
                fetch_lists: list[tuple[str, Any]] = []
                for url, data in payloads:
                    if FETCH_LIST_SUBSTRING not in url:
                        continue
                    fetch_lists.append((url, data))
                    try:
                        if "BANK_OFFER" in json.dumps(data).upper():
                            bankish.append((url, data))
                    except Exception:
                        pass
                if bankish:
                    result.payload = bankish[-1][1]
                    result.matched_url = bankish[-1][0]
                    result.notes.append(
                        f"Selected BANK_OFFER fetch-list from {result.matched_url} "
                        f"({len(bankish)}/{len(fetch_lists)} fetch-list responses)."
                    )
                elif result.payload is None and fetch_lists:
                    result.payload = fetch_lists[-1][1]
                    result.matched_url = fetch_lists[-1][0]
                    result.notes.append(
                        f"Selected fetch-list from {result.matched_url} "
                        f"({len(payloads)} coupon-ish responses seen)."
                    )
                elif result.payload is None:
                    result.payload = payloads[-1][1]
                    result.matched_url = payloads[-1][0]
                    result.notes.append(
                        f"Selected fallback payload from {result.matched_url}."
                    )

            # Only overwrite debug capture when we actually got responses
            if payloads:
                try:
                    debug_path = Path("output") / "raw_coupon_responses.json"
                    debug_path.parent.mkdir(parents=True, exist_ok=True)
                    debug_path.write_text(
                        json.dumps(
                            [{"url": u, "payload": d} for u, d in payloads],
                            indent=2,
                            ensure_ascii=False,
                            default=str,
                        )[:2_000_000],
                        encoding="utf-8",
                    )
                    result.notes.append(f"Wrote {debug_path} ({len(payloads)} responses).")
                except Exception as exc:
                    result.notes.append(f"Could not write raw responses: {exc}")
            else:
                result.notes.append("Skipped writing raw_coupon_responses.json (0 responses; kept prior file).")
                try:
                    page.screenshot(path="artifacts/debug_no_coupons.png", full_page=False)
                    result.notes.append("Saved artifacts/debug_no_coupons.png")
                except Exception:
                    pass

            if result.payload is None:
                # Last resort: DOM parse of visible offer cards
                dom_offers = self._dom_scrape_payment_offers(page)
                if dom_offers:
                    result.payload = {"offers": dom_offers, "source": "dom_fallback"}
                    result.notes.append("Used DOM fallback because no coupon XHR was captured.")
                else:
                    result.notes.append("No coupon API payload and no DOM offers captured.")

            if self.storage_state:
                context.storage_state(path=str(self.storage_state))
                result.storage_state_path = str(self.storage_state)
        finally:
            context.close()

        return result

    def _ensure_payment_offers_and_capture(
        self,
        page: Page,
        result: BrowserResult,
        payloads: list[tuple[str, Any]],
    ) -> None:
        """Open Payment Offers inside the coupons drawer and wait for fetch-list."""
        # If coupons panel never opened, force cart page then retry entry
        if not self._coupon_drawer_open(page):
            result.notes.append("Coupon drawer not open; forcing /cart + coupons entry.")
            try:
                page.goto("https://www.zepto.com/cart", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
            except Exception as exc:
                result.notes.append(f"/cart navigation failed: {exc}")
            self._open_coupons_panel(page, result)

        attempts = (
            "Payment Offers",
            "View all payment offers",
            "Bank Offers",
            "Card Offers",
        )
        for attempt in range(2):
            before = len(payloads)
            try:
                with page.expect_response(
                    lambda r: FETCH_LIST_SUBSTRING in r.url and r.ok,
                    timeout=25_000,
                ) as resp_info:
                    clicked = self._open_payment_offers_tab(page, result, labels=attempts)
                    if not clicked:
                        # Re-click coupons then payment tab
                        self._open_coupons_panel(page, result)
                        self._open_payment_offers_tab(page, result, labels=attempts)
                resp = resp_info.value
                try:
                    data = resp.json()
                    payloads.append((resp.url, data))
                    result.payload = data
                    result.matched_url = resp.url
                    result.notes.append("Captured fetch-list via expect_response.")
                    return
                except Exception as exc:
                    result.notes.append(f"fetch-list response not JSON: {exc}")
            except Exception as exc:
                result.notes.append(f"expect_response fetch-list missed (try {attempt + 1}): {exc}")
                self._open_payment_offers_tab(page, result, labels=attempts)
                page.wait_for_timeout(4000)
                if len(payloads) > before:
                    result.notes.append("fetch-list arrived via response listener after wait.")
                    return
        # Final settle wait for listener-only captures
        page.wait_for_timeout(3000)

    def _coupon_drawer_open(self, page: Page) -> bool:
        try:
            dlg = page.locator('[role="dialog"], [data-vaul-drawer]')
            for i in range(min(dlg.count(), 4)):
                el = dlg.nth(i)
                if not el.is_visible():
                    continue
                text = (el.inner_text(timeout=1000) or "").lower()
                if "coupon" in text or "payment offer" in text or "offer" in text:
                    return True
        except Exception:
            pass
        return False

    def _capture_store_id(self, page: Page) -> str | None:
        try:
            return page.evaluate(
                """() => {
                  try {
                    const keys = Object.keys(localStorage);
                    for (const k of keys) {
                      const v = localStorage.getItem(k) || '';
                      if (v.includes('storeId') || v.includes('store_id')) {
                        const m = v.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
                        if (m) return m[0];
                      }
                    }
                  } catch (e) {}
                  return null;
                }"""
            )
        except Exception:
            return None

    def _ensure_location(self, page: Page, result: BrowserResult) -> None:
        try:
            trigger = page.get_by_text("Select Location", exact=False)
            if not trigger.count() or not trigger.first.is_visible():
                result.notes.append("Location already set or selector not visible.")
                return
            trigger.first.click(timeout=3000)
            page.wait_for_timeout(800)
            box = page.locator(
                'input[placeholder*="location" i], input[placeholder*="search" i], '
                'input[placeholder*="deliver" i], input[type="search"], '
                'input[type="text"]:visible'
            ).first
            box.fill("Whitefield Bengaluru", timeout=5000)
            page.wait_for_timeout(1500)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            for label in ("Confirm", "Continue", "Save", "Yes, proceed"):
                btn = page.get_by_role("button", name=re.compile(label, re.I))
                if btn.count() and btn.first.is_visible():
                    btn.first.click()
                    page.wait_for_timeout(1000)
                    break
            result.notes.append("Attempted to set delivery location via UI.")
        except Exception as exc:
            result.notes.append(f"Location UI step skipped: {exc}")

    def _add_first_item(self, page: Page, result: BrowserResult) -> None:
        try:
            btn = page.get_by_role("button", name=re.compile(r"^ADD$", re.I)).first
            btn.wait_for(state="visible", timeout=10_000)
            # Prefer JS click: native click often waits on product-page navigations forever
            btn.evaluate("el => el.click()")
            page.wait_for_timeout(2500)
            # If a variant / confirm modal appears, try confirm add
            for label in ("ADD", "Add to cart", "Confirm"):
                extra = page.get_by_role("button", name=re.compile(rf"^{label}$", re.I))
                if extra.count() and extra.first.is_visible():
                    try:
                        extra.first.evaluate("el => el.click()")
                        page.wait_for_timeout(1000)
                    except Exception:
                        pass
                    break
            result.notes.append("Added an item to cart.")
        except Exception as exc:
            result.notes.append(f"Could not add item automatically: {exc}")

    def _open_cart(self, page: Page, result: BrowserResult) -> None:
        try:
            if "/pn/" in page.url or "/pvid/" in page.url:
                page.goto("https://www.zepto.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
            # Dedicated cart route is more reliable than the header drawer
            page.goto("https://www.zepto.com/cart", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            result.notes.append("Opened /cart.")
            return
        except Exception as exc:
            result.notes.append(f"/cart open failed: {exc}")
        try:
            cart = page.get_by_text("Cart", exact=True).first
            if not cart.is_visible():
                cart = page.locator(
                    '[aria-label*="cart" i], a[href*="cart" i], button:has-text("Cart")'
                ).first
            cart.click(timeout=8000, force=True)
            page.wait_for_timeout(2000)
            result.notes.append("Opened cart drawer.")
        except Exception as exc:
            result.notes.append(f"Cart open failed: {exc}")

    def _coupons_accessible(self, page: Page) -> bool:
        try:
            login_gate = page.get_by_text("Login to view coupons", exact=False)
            for i in range(min(login_gate.count(), 5)):
                if login_gate.nth(i).is_visible():
                    return False
            if page.get_by_text("Payment Offers", exact=False).first.is_visible():
                return True
            view = page.get_by_text("View coupons", exact=False)
            for i in range(min(view.count(), 5)):
                if view.nth(i).is_visible():
                    return True
        except Exception:
            pass
        return False

    def _click_login(self, page: Page) -> None:
        for label in ("Login to Proceed", "Login to view coupons", "Login"):
            try:
                loc = page.get_by_text(label, exact=False)
                for i in range(min(loc.count(), 5)):
                    el = loc.nth(i)
                    if el.is_visible():
                        el.click(timeout=3000)
                        return
            except Exception:
                continue

    def _wait_until_coupons_or_timeout(
        self,
        page: Page,
        result: BrowserResult,
        seconds: int,
        *,
        phone: str | None = None,
    ) -> None:
        if phone:
            page.wait_for_timeout(1500)
            self._fill_phone_and_continue(page, phone)
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._coupons_accessible(page):
                result.notes.append("Login appears complete; coupons UI accessible.")
                return
            try:
                if page.get_by_text("Payment Offers", exact=False).first.is_visible():
                    result.notes.append("Login appears complete; Payment Offers visible.")
                    return
            except Exception:
                pass
            # Auth cookie heuristic — user may have logged in without coupons panel open
            try:
                ok, detail = self._auth_cookie_status(page.context)
                if ok:
                    result.notes.append(f"Login detected via cookies ({detail}).")
                    self._open_cart(page, result)
                    self._open_coupons_panel(page, result)
                    if self._coupons_accessible(page):
                        result.notes.append("Coupons accessible after cookie-auth retry.")
                        return
                    # Cookie auth is enough to stop waiting even if UI lags
                    print(f"Authenticated via cookies ({detail}); continuing.", flush=True)
                    return
            except Exception:
                pass
            page.wait_for_timeout(2000)
        result.notes.append("Timed out waiting for login/coupons UI.")

    def _open_coupons_panel(self, page: Page, result: BrowserResult) -> None:
        labels = (
            "View coupons",
            "View Coupons",
            "Apply Coupon",
            "Apply coupon",
            "Coupons & offers",
            "Coupon & Offers",
            "Coupons & Offers",
            "View all coupons",
            "Coupons",
        )
        if "/cart" not in page.url:
            try:
                page.goto("https://www.zepto.com/cart", wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            except Exception:
                pass
        for label in labels:
            try:
                loc = page.get_by_text(label, exact=False)
                for i in range(min(loc.count(), 8)):
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    el.evaluate("node => node.click()")
                    page.wait_for_timeout(2500)
                    result.notes.append(f"Clicked '{label}'.")
                    return
            except Exception:
                continue
        try:
            clicked = page.evaluate(
                """() => {
                  const re = /view coupons|apply coupon|coupons?\\s*&\\s*offers/i;
                  const nodes = Array.from(document.querySelectorAll('button, a, div, span'));
                  for (const n of nodes) {
                    const t = (n.innerText || '').trim();
                    if (!t || t.length > 48) continue;
                    if (!re.test(t)) continue;
                    const r = n.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) continue;
                    n.click();
                    return t;
                  }
                  return null;
                }"""
            )
            if clicked:
                page.wait_for_timeout(2500)
                result.notes.append(f"JS-clicked coupons control '{clicked}'.")
                return
        except Exception as exc:
            result.notes.append(f"JS coupons click failed: {exc}")
        result.notes.append("Could not find coupons entry point.")

    def _open_payment_offers_tab(
        self,
        page: Page,
        result: BrowserResult,
        labels: tuple[str, ...] | None = None,
    ) -> bool:
        labels = labels or (
            "Payment Offers",
            "View all payment offers",
            "Bank Offers",
            "Card Offers",
        )
        try:
            try:
                page.locator(".animate-pulse").first.wait_for(state="hidden", timeout=8000)
            except Exception:
                page.wait_for_timeout(1500)

            # Prefer drawer/dialog so we don't click unrelated homepage text
            containers = [
                page.locator('[role="dialog"]:visible, [data-vaul-drawer]:visible'),
                page.locator("body"),
            ]
            for container in containers:
                for label in labels:
                    loc = container.get_by_text(label, exact=False)
                    for i in range(min(loc.count(), 6)):
                        el = loc.nth(i)
                        try:
                            if not el.is_visible():
                                continue
                            el.evaluate("node => node.click()")
                            page.wait_for_timeout(3500)
                            result.notes.append(f"Opened payment offers via '{label}'.")
                            return True
                        except Exception:
                            continue
            result.notes.append("Payment Offers tab/link not clicked.")
            return False
        except Exception as exc:
            result.notes.append(f"Payment Offers tab not clicked: {exc}")
            return False

    def _dom_scrape_payment_offers(self, page: Page) -> list[dict[str, Any]]:
        try:
            return page.evaluate(
                """() => {
                  const root = document.body;
                  const texts = Array.from(root.querySelectorAll('button, div, span, p, h1, h2, h3'))
                    .map(el => (el.innerText || '').trim())
                    .filter(Boolean);
                  // Very loose scrape: look for promo-code-like tokens near bank keywords
                  const codeRe = /\\bZEP[A-Z0-9]{3,}\\b/;
                  const bankRe = /(bank|visa|mastercard|hdfc|icici|sbi|axis|idfc|hsbc|kotak|rupay)/i;
                  const offers = [];
                  const seen = new Set();
                  for (const t of texts) {
                    if (t.length > 180) continue;
                    if (codeRe.test(t) || (bankRe.test(t) && /%|₹|off|flat|cashback/i.test(t))) {
                      const key = t.slice(0, 120);
                      if (seen.has(key)) continue;
                      seen.add(key);
                      const code = (t.match(codeRe) || [null])[0];
                      offers.push({
                        title: t.split('\\n')[0].slice(0, 120),
                        description: t,
                        couponCode: code,
                        offerType: bankRe.test(t) ? 'BANK_OFFER' : 'UNKNOWN',
                        source: 'dom'
                      });
                    }
                  }
                  return offers.slice(0, 40);
                }"""
            )
        except Exception:
            return []


def is_fetch_list_url(url: str) -> bool:
    path = urlparse(url).path
    return FETCH_LIST_SUBSTRING in path or FETCH_LIST_SUBSTRING in url
