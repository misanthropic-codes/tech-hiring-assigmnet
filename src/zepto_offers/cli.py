from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .browser import DEFAULT_CAPTURE_PATH, OfferBrowser
from .filter import filter_bank_offers
from .models import OfferReport, StoreContext
from .normalize import extract_raw_offers, normalize_offers
from .replay import replay_fetch_list


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "fixtures" / "sample_fetch_list.json"


def _build_report(
    *,
    payload: object,
    mode: str,
    store: StoreContext,
    notes: list[str] | None = None,
) -> OfferReport:
    raw_items = extract_raw_offers(payload)
    normalized = normalize_offers(payload)
    kept, excluded = filter_bank_offers(normalized, raw_items)
    return OfferReport(
        fetched_at=datetime.now(timezone.utc),
        mode=mode,  # type: ignore[arg-type]
        store=store,
        offers=kept,
        excluded_count=excluded,
        notes=notes or [],
        raw_meta={"raw_offer_count": len(raw_items)},
    )


def cmd_fixture(args: argparse.Namespace) -> int:
    path = Path(args.fixture or DEFAULT_FIXTURE)
    payload = json.loads(path.read_text())
    report = _build_report(
        payload=payload,
        mode="fixture",
        store=StoreContext(
            id=payload.get("storeId") or payload.get("store_id"),
            lat=args.lat,
            lng=args.lng,
            name=payload.get("storeName"),
        ),
        notes=[f"Loaded fixture {path}"],
    )
    return _emit(report, args)


def cmd_live(args: argparse.Namespace) -> int:
    storage = args.storage_state or os.getenv("ZEPTO_STORAGE_STATE") or "storage_state.json"
    headed = not args.headless
    phone = args.phone or os.getenv("ZEPTO_PHONE")
    with OfferBrowser(
        lat=args.lat,
        lng=args.lng,
        headed=headed,
        storage_state=storage,
        phone=phone,
    ) as browser:
        result = browser.fetch_coupon_payload(wait_for_login=args.wait_login)
    if result.payload is None:
        print(
            json.dumps(
                {
                    "error": "No coupon payload captured",
                    "notes": result.notes,
                    "hint": "Run: python -m zepto_offers --login  (complete OTP), then retry --live",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    report = _build_report(
        payload=result.payload,
        mode="live",
        store=StoreContext(id=result.store_id, lat=result.lat, lng=result.lng),
        notes=result.notes
        + ([f"matched_url={result.matched_url}"] if result.matched_url else []),
    )
    return _emit(report, args)


def cmd_replay(args: argparse.Namespace) -> int:
    capture = args.capture_request or os.getenv("ZEPTO_CAPTURE_REQUEST") or str(DEFAULT_CAPTURE_PATH)
    storage = args.storage_state or os.getenv("ZEPTO_STORAGE_STATE") or "storage_state.json"
    print(f"Replaying fetch-list without browser (capture={capture})…", flush=True)
    try:
        result = replay_fetch_list(capture_path=capture, storage_state=storage)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    if result.payload is None:
        print(
            json.dumps(
                {
                    "error": "Replay returned no usable payload",
                    "status_code": result.status_code,
                    "notes": result.notes,
                    "hint": "Run --live once to refresh output/captured_fetch_list_request.json, then --replay",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    report = _build_report(
        payload=result.payload,
        mode="replay",
        store=StoreContext(id=result.store_id, lat=result.lat or args.lat, lng=result.lng or args.lng),
        notes=result.notes
        + ([f"matched_url={result.matched_url}"] if result.matched_url else [])
        + ([f"http_status={result.status_code}"] if result.status_code is not None else []),
    )
    return _emit(report, args)


def cmd_login(args: argparse.Namespace) -> int:
    storage = Path(args.storage_state or os.getenv("ZEPTO_STORAGE_STATE") or "storage_state.json")
    phone = args.phone or os.getenv("ZEPTO_PHONE")
    with OfferBrowser(lat=args.lat, lng=args.lng, headed=True, storage_state=None, phone=phone) as browser:
        path = browser.save_login_state(storage, phone=phone)
    print(f"Saved Playwright storage state to {path}", flush=True)
    print("Next: python -m zepto_offers --live   # also saves replay capture", flush=True)
    print("Then: python -m zepto_offers --replay --out output/offers.json", flush=True)
    return 0


def _emit(report: OfferReport, args: argparse.Namespace) -> int:
    data = report.model_dump(mode="json")
    text = json.dumps(data, indent=2, ensure_ascii=False)

    offers = report.offers
    print(f"\n=== Zepto Payment Offers ({len(offers)} kept, {report.excluded_count} excluded) ===", flush=True)
    if not offers:
        print("(no bank/card offers)", flush=True)
    else:
        for i, o in enumerate(offers, 1):
            disc = o.discount
            if disc.type == "percent" and disc.value is not None:
                disc_s = f"{disc.value:g}%" + (f" up to ₹{disc.max_cap:g}" if disc.max_cap else "")
            elif disc.value is not None:
                disc_s = f"₹{disc.value:g}" + (f" ({disc.type})" if disc.type not in {"flat", "unknown"} else "")
            else:
                disc_s = disc.raw or disc.type
            mov = f"MOV ₹{o.min_order_value:g}" if o.min_order_value is not None else "MOV —"
            code = o.promo_code or "—"
            print(
                f"{i:3}. [{o.status}] {o.title} | {o.bank_or_card} | {disc_s} | {code} | {mov}",
                flush=True,
            )
    print("=== end offers ===\n", flush=True)

    print(text, flush=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr, flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zepto-offers",
        description="Extract Zepto checkout bank/card payment offers (network-first).",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Open Zepto, intercept APIs, save replay capture")
    mode.add_argument(
        "--replay",
        action="store_true",
        help="HTTP-only replay of captured fetch-list (no browser)",
    )
    mode.add_argument("--fixture", nargs="?", const=str(DEFAULT_FIXTURE), help="Use offline fixture JSON")
    mode.add_argument("--login", action="store_true", help="Interactive OTP login; save storage_state.json")

    p.add_argument("--wait-login", action="store_true", help="In --live, wait for user to finish OTP")
    p.add_argument("--phone", help="Mobile number to prefill on login (OTP still entered in browser)")
    p.add_argument("--storage-state", help="Path to Playwright storage state JSON")
    p.add_argument(
        "--capture-request",
        help=f"Path to captured fetch-list request JSON (default: {DEFAULT_CAPTURE_PATH})",
    )
    p.add_argument("--headless", action="store_true", help="Force headless (may fail AWS WAF)")
    p.add_argument("--lat", type=float, default=float(os.getenv("ZEPTO_LAT", "12.96902")))
    p.add_argument("--lng", type=float, default=float(os.getenv("ZEPTO_LNG", "77.75395")))
    p.add_argument("--out", help="Also write JSON report to this path")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.login:
        return cmd_login(args)
    if args.live:
        return cmd_live(args)
    if args.replay:
        return cmd_replay(args)
    # Default: fixture mode
    if not args.fixture:
        args.fixture = str(DEFAULT_FIXTURE)
    return cmd_fixture(args)


if __name__ == "__main__":
    raise SystemExit(main())
