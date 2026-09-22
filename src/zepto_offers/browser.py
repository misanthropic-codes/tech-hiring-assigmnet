"""Playwright bootstrap: login, open Payment Offers, intercept fetch-list."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, Playwright, sync_playwright


FETCH_LIST_SUBSTRING = "cfs/api/v1/cart/coupons/fetch-list"
DEFAULT_CAPTURE_PATH = Path("output") / "captured_fetch_list_request.json"

_SKIP_REQUEST_HEADERS = {
    "content-length",
    "host",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "accept-encoding",
    "cookie",
}


@dataclass
class BrowserResult:
    payload: Any | None = None
    matched_url: str | None = None
    store_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    notes: list[str] = field(default_factory=list)
    storage_state_path: str | None = None
    captured_request: dict[str, Any] | None = None


def _cookie_jar(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain"),
            "path": c.get("path") or "/",
        }
        for c in cookies
        if c.get("name") and c.get("value") is not None
    ]


def serialize_fetch_list_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    post_data: str | None,
    cookies: list[dict[str, Any]],
    store_id: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    response_status: int | None = None,
) -> dict[str, Any]:
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": url,
        "method": (method or "GET").upper(),
        "headers": {
            str(k): str(v)
            for k, v in headers.items()
            if str(k).lower() not in _SKIP_REQUEST_HEADERS
        },
        "post_data": post_data,
        "cookies": _cookie_jar(cookies),
        "store_id": store_id,
        "lat": lat,
        "lng": lng,
        "response_status": response_status,
        "endpoint": FETCH_LIST_SUBSTRING,
    }


def save_captured_request(capture: dict[str, Any], path: str | Path | None = None) -> Path:
    path = Path(path or DEFAULT_CAPTURE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(capture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_authenticated_from_cookies(cookies: list[dict[str, Any]]) -> bool:
    by_name = {str(c.get("name", "")): str(c.get("value", "")) for c in cookies}
    lower = {k.lower(): v for k, v in by_name.items()}
    access = lower.get("accesstoken") or lower.get("access_token") or ""
    refresh = lower.get("refreshtoken") or lower.get("refresh_token") or ""
    user_id = lower.get("user_id") or lower.get("userid") or ""
    is_auth = (by_name.get("isAuth") or lower.get("isauth") or "").strip().lower()

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
        for kwargs in ({"channel": "chrome"}, {}):
            try:
                self._browser = self._playwright.chromium.launch(
                    headless=not self.headed,
                    args=launch_args,
                    **kwargs,
                )
                return self
            except Exception as exc:
                last_err = exc
        raise RuntimeError(f"Could not launch Chrome/Chromium: {last_err}") from last_err

    def __exit__(self, *exc: object) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _new_page(self) -> tuple[Any, Page]:
        assert self._browser is not None
        kwargs: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
            "geolocation": {"latitude": self.lat, "longitude": self.lng},
            "permissions": ["geolocation"],
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if self.storage_state and self.storage_state.exists():
            kwargs["storage_state"] = str(self.storage_state)
        context = self._browser.new_context(**kwargs)
        page = context.new_page()
        page.set_default_timeout(self.timeout_ms)
        return context, page

    def _auth_ok(self, context: Any) -> bool:
        try:
            return _is_authenticated_from_cookies(context.cookies())
        except Exception:
            return False

    def save_login_state(self, path: str | Path, *, phone: str | None = None) -> Path:
        path = Path(path)
        context, page = self._new_page()
        page.goto("https://www.zepto.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        if self._auth_ok(context):
            print("Already logged in. Saving session.", flush=True)
            context.storage_state(path=str(path))
            context.close()
            return path

        print("Complete phone + OTP in the browser (up to 5 minutes)…", flush=True)
        try:
            page.get_by_role("button", name=re.compile(r"login", re.I)).first.click(timeout=5000)
        except Exception:
            pass
        if phone:
            self._fill_phone(page, phone)

        deadline = time.time() + 300
        while time.time() < deadline:
            if self._auth_ok(context):
                print("Login detected.", flush=True)
                break
            page.wait_for_timeout(2000)

        context.storage_state(path=str(path))
        context.close()
        return path

    def _fill_phone(self, page: Page, phone: str) -> None:
        digits = re.sub(r"\D", "", phone)[-10:]
        try:
            page.wait_for_timeout(1500)
            phone_input = page.locator(
                'input[placeholder*="Phone Number" i], input[type="tel"]'
            ).first
            phone_input.wait_for(state="visible", timeout=20_000)
            phone_input.fill(digits)
            btn = page.get_by_role("button", name=re.compile(r"^Continue$", re.I))
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=5000)
            print("Phone submitted; enter OTP in the browser.", flush=True)
        except Exception as exc:
            print(f"Could not auto-fill phone ({exc}). Enter {digits} + OTP manually.", flush=True)

    def fetch_coupon_payload(self, *, wait_for_login: bool = False) -> BrowserResult:
        context, page = self._new_page()
        result = BrowserResult(lat=self.lat, lng=self.lng)
        payloads: list[tuple[str, Any]] = []

        def on_response(resp: Any) -> None:
            if FETCH_LIST_SUBSTRING not in resp.url:
                return
            try:
                data = resp.json()
            except Exception:
                return
            payloads.append((resp.url, data))
            try:
                headers = dict(resp.request.headers)
                post_data = resp.request.post_data
            except Exception:
                headers, post_data = {}, None
            snap = serialize_fetch_list_request(
                url=resp.url,
                method=getattr(resp.request, "method", "GET") or "GET",
                headers=headers,
                post_data=post_data,
                cookies=context.cookies(),
                lat=self.lat,
                lng=self.lng,
                response_status=resp.status,
            )
            prefer = "BANK_OFFER" in json.dumps(data).upper()
            if result.payload is None or prefer:
                result.payload = data
                result.matched_url = resp.url
                result.captured_request = snap

        page.on("response", on_response)

        try:
            page.goto("https://www.zepto.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            if self._auth_ok(context):
                print("Already logged in via cookies — skipping OTP.", flush=True)

            self._add_first_item(page)
            self._open_cart(page)

            if not self._auth_ok(context):
                if wait_for_login or self.phone:
                    print("Please log in (OTP). Waiting…", flush=True)
                    self._click_login(page)
                    self._wait_for_login(page, result, seconds=300)
                else:
                    result.notes.append("Not logged in. Use --login or --wait-login --phone.")

            self._open_coupons_panel(page)
            self._open_payment_offers(page)

            bankish = [
                (u, d) for u, d in payloads if "BANK_OFFER" in json.dumps(d).upper()
            ]
            if bankish:
                result.matched_url, result.payload = bankish[-1][0], bankish[-1][1]
            elif result.payload is None and payloads:
                result.matched_url, result.payload = payloads[-1]

            if result.captured_request:
                result.captured_request["cookies"] = _cookie_jar(context.cookies())
                out = save_captured_request(result.captured_request)
                result.notes.append(f"Saved capture → {out}")
                print(f"Saved replayable fetch-list request to {out}", flush=True)
            elif not payloads:
                result.notes.append("No fetch-list responses captured.")

            if self.storage_state:
                context.storage_state(path=str(self.storage_state))
                result.storage_state_path = str(self.storage_state)
        finally:
            context.close()

        return result

    def _add_first_item(self, page: Page) -> None:
        try:
            btn = page.get_by_role("button", name=re.compile(r"^ADD$", re.I)).first
            btn.wait_for(state="visible", timeout=10_000)
            btn.evaluate("el => el.click()")
            page.wait_for_timeout(2000)
        except Exception:
            pass

    def _open_cart(self, page: Page) -> None:
        page.goto("https://www.zepto.com/cart", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

    def _click_login(self, page: Page) -> None:
        for label in ("Login to Proceed", "Login"):
            try:
                loc = page.get_by_text(label, exact=False)
                for i in range(min(loc.count(), 4)):
                    el = loc.nth(i)
                    if el.is_visible():
                        el.click(timeout=3000)
                        return
            except Exception:
                continue

    def _wait_for_login(self, page: Page, result: BrowserResult, seconds: int) -> None:
        if self.phone:
            page.wait_for_timeout(1000)
            self._fill_phone(page, self.phone)
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._auth_ok(page.context):
                result.notes.append("Login detected.")
                self._open_cart(page)
                return
            page.wait_for_timeout(2000)
        result.notes.append("Timed out waiting for login.")

    def _open_coupons_panel(self, page: Page) -> None:
        if "/cart" not in page.url:
            self._open_cart(page)
        for label in ("View coupons", "Apply Coupon", "Coupons & Offers"):
            try:
                loc = page.get_by_text(label, exact=False)
                for i in range(min(loc.count(), 6)):
                    el = loc.nth(i)
                    if el.is_visible():
                        el.evaluate("node => node.click()")
                        page.wait_for_timeout(2000)
                        return
            except Exception:
                continue

    def _open_payment_offers(self, page: Page) -> None:
        try:
            page.locator(".animate-pulse").first.wait_for(state="hidden", timeout=6000)
        except Exception:
            page.wait_for_timeout(1000)

        def _click_tab() -> bool:
            for root in (
                page.locator('[role="dialog"]:visible, [data-vaul-drawer]:visible'),
                page.locator("body"),
            ):
                loc = root.get_by_text("Payment Offers", exact=False)
                for i in range(min(loc.count(), 4)):
                    el = loc.nth(i)
                    try:
                        if el.is_visible():
                            el.evaluate("node => node.click()")
                            return True
                    except Exception:
                        continue
            return False

        try:
            with page.expect_response(
                lambda r: FETCH_LIST_SUBSTRING in r.url and r.ok,
                timeout=25_000,
            ):
                if not _click_tab():
                    self._open_coupons_panel(page)
                    _click_tab()
        except Exception:
            _click_tab()
            page.wait_for_timeout(4000)
