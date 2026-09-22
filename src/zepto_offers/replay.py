"""Replay a captured Zepto fetch-list request without opening a browser."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .browser import DEFAULT_CAPTURE_PATH, FETCH_LIST_SUBSTRING


@dataclass
class ReplayResult:
    payload: Any | None = None
    matched_url: str | None = None
    status_code: int | None = None
    store_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    notes: list[str] = field(default_factory=list)


def load_capture(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path or os.getenv("ZEPTO_CAPTURE_REQUEST") or DEFAULT_CAPTURE_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"No captured request at {path}. Run: PYTHONPATH=src python -m zepto_offers --live "
            f"(this saves {DEFAULT_CAPTURE_PATH}), then retry --replay."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _cookies_from_storage_state(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("cookies") or [])


def _merge_cookies(
    capture_cookies: list[dict[str, Any]],
    storage_cookies: list[dict[str, Any]],
) -> dict[str, str]:
    """storage_state wins over capture snapshot for overlapping names."""
    merged: dict[str, str] = {}
    for c in capture_cookies or []:
        name = c.get("name")
        if name and c.get("value") is not None:
            merged[str(name)] = str(c["value"])
    for c in storage_cookies or []:
        name = c.get("name")
        if name and c.get("value") is not None:
            merged[str(name)] = str(c["value"])
    return merged


def replay_fetch_list(
    *,
    capture_path: str | Path | None = None,
    storage_state: str | Path | None = None,
    timeout_s: float = 45.0,
) -> ReplayResult:
    """HTTP-only replay of the last captured fetch-list call."""
    result = ReplayResult()
    capture = load_capture(capture_path)
    result.notes.append(f"Loaded capture from {capture_path or DEFAULT_CAPTURE_PATH}")
    result.store_id = capture.get("store_id")
    result.lat = capture.get("lat")
    result.lng = capture.get("lng")

    url = capture.get("url") or ""
    if FETCH_LIST_SUBSTRING not in url:
        result.notes.append(f"Warning: capture URL may not be fetch-list: {url}")

    method = (capture.get("method") or "GET").upper()
    headers = dict(capture.get("headers") or {})
    # Prefer a browser-like UA if missing
    headers.setdefault(
        "user-agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    headers.setdefault("accept", "application/json, text/plain, */*")
    headers.setdefault("origin", "https://www.zepto.com")
    headers.setdefault("referer", "https://www.zepto.com/")

    storage = storage_state or os.getenv("ZEPTO_STORAGE_STATE") or "storage_state.json"
    cookie_dict = _merge_cookies(capture.get("cookies") or [], _cookies_from_storage_state(storage))
    if not cookie_dict:
        result.notes.append("No cookies available — replay will likely 401/429.")
    else:
        result.notes.append(f"Using {len(cookie_dict)} cookies (capture ∪ storage_state).")

    post_data = capture.get("post_data")
    content = None
    if post_data is not None and method in {"POST", "PUT", "PATCH"}:
        content = post_data if isinstance(post_data, (str, bytes)) else json.dumps(post_data)
        headers.setdefault("content-type", "application/json")

    try:
        timeout = httpx.Timeout(timeout_s, connect=min(15.0, timeout_s))
        with httpx.Client(timeout=timeout, follow_redirects=True, http2=False) as client:
            resp = client.request(
                method,
                url,
                headers=headers,
                cookies=cookie_dict,
                content=content,
            )
    except httpx.HTTPError as exc:
        result.notes.append(f"HTTP error during replay: {exc}")
        return result

    result.status_code = resp.status_code
    result.matched_url = str(resp.url)
    result.notes.append(f"Replay HTTP {resp.status_code} from {result.matched_url}")

    if resp.status_code in {401, 403}:
        result.notes.append("Auth failed — re-run --live or --login to refresh session/capture.")
        return result
    if resp.status_code in {429, 202}:
        result.notes.append(
            "WAF/rate-limit response — browser bootstrap may be required to refresh tokens."
        )
        # Still try to parse body if JSON
    try:
        result.payload = resp.json()
    except Exception:
        result.notes.append(f"Non-JSON body ({len(resp.content)} bytes): {resp.text[:200]!r}")
        result.payload = None
        return result

    if not result.payload:
        result.notes.append("Empty JSON payload.")
    elif isinstance(result.payload, dict) and not (
        result.payload.get("pageLayout") or result.payload.get("coupons") or result.payload.get("data")
    ):
        # Might be an error envelope
        result.notes.append(f"Unexpected JSON keys: {list(result.payload)[:12]}")

    return result
