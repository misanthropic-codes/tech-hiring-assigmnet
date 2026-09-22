"""Normalize Zepto fetch-list payloads into Offer models."""

from __future__ import annotations

import re
from typing import Any

from .models import Discount, Offer, OfferStatus


NON_BANK_TYPES = {
    "WALLET_OFFER",
    "WALLET",
    "DELIVERY",
    "PRODUCT",
    "PROMO_CASH",
    "MEMBERSHIP",
    "PASS",
    "ZEPTO_CASH",
    "COUPON",
}

BANK_KEYWORDS = re.compile(
    r"\b("
    r"bank|visa|mastercard|master\s*card|amex|american\s*express|rupay|"
    r"hdfc|icici|sbi|axis|idfc|hsbc|kotak|yes\s*bank|rbl|indusind|induslnd|"
    r"federal|au\s*bank|au\s*small|bob|bank\s*of\s*baroda|pnb|punjab\s*national|"
    r"union\s*bank|idbi|indian\s*bank|indian\s*overseas|jana\s*bank|dbs|"
    r"credit\s*card|debit\s*card|card\s*offer|novio"
    r")\b",
    re.I,
)

CARD_KEYWORDS = re.compile(
    r"\b("
    r"credit\s*cards?|debit\s*cards?|visa|mastercard|master\s*card|"
    r"amex|american\s*express|rupay|prepaid\s*card"
    r")\b",
    re.I,
)

NON_BANK_KEYWORDS = re.compile(
    r"\b("
    r"amazon\s*pay|amazon\s*pay\s*later|phonepe|google\s*pay|gpay|paytm|"
    r"mobikwik|freecharge|bhim|jupiter|bajaj\s*pay|"
    r"wallet|zepto\s*cash|promo\s*cash|delivery\s*fee|handling\s*fee|"
    r"membership|zepto\s*pass|cashback\s*on\s*upi|\bupi\b|"
    r"pay\s*later|811\s*app"
    r")\b",
    re.I,
)

_AMOUNT = r"([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
FLAT_RE = re.compile(
    rf"(?:flat\s*|save\s*)?[₹rs\.]*\s*{_AMOUNT}\s*(?:!|\s|$|off|cashback)",
    re.I,
)
PERCENT_RE = re.compile(
    rf"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:off|cashback)?"
    rf"(?:\s*(?:up\s*to|upto)\s*[₹rs\.]*\s*{_AMOUNT})?",
    re.I,
)
MOV_RE = re.compile(
    rf"(?:min(?:imum)?(?:\s*order)?(?:\s*value)?|mov|shop\s*for|cart\s*value|orders?\s*above|above)"
    rf"[^\d₹rs]*[₹rs\.]*\s*{_AMOUNT}",
    re.I,
)
SHOP_MORE_RE = re.compile(
    rf"shop\s*for\s*[₹rs\.]*\s*{_AMOUNT}\s*more",
    re.I,
)


def _parse_amount(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _dig(obj: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        cur: Any = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return default


def _dedupe_offers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uniq: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(
            item.get("id")
            or item.get("couponId")
            or item.get("couponCode")
            or item.get("promoCode")
            or item.get("title")
            or id(item)
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def _textish(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("text") not in (None, ""):
        return str(value["text"])
    return None


def _flatten_coupon_card(item: dict[str, Any]) -> dict[str, Any]:
    meta = _dig(item, "couponButton.action.actionMeta") or {}
    if not isinstance(meta, dict):
        meta = {}
    heading = _textish(item.get("heading")) or _textish(item.get("title"))
    subheading = _textish(item.get("subheading")) or _textish(item.get("subtitle"))
    code = (
        _textish(item.get("couponCode"))
        or meta.get("couponCode")
        or meta.get("promoCode")
        or item.get("promoCode")
    )
    terms_block = item.get("termsAndConditions") if isinstance(item.get("termsAndConditions"), dict) else {}
    terms = terms_block.get("terms") if isinstance(terms_block, dict) else item.get("terms")
    terms_desc = terms_block.get("description") if isinstance(terms_block, dict) else None

    flat = {
        "title": heading or "Untitled offer",
        "subtitle": subheading or "",
        "description": subheading or terms_desc or "",
        "couponCode": code,
        "promoCode": code,
        "offerType": meta.get("couponType") or meta.get("offerType") or item.get("offerType"),
        "id": meta.get("couponId") or item.get("couponId") or item.get("id"),
        "status": _dig(item, "couponButton.couponState") or _dig(item, "couponButton.state"),
        "terms": terms,
        "minimumOrderValue": None,
        "bankName": None,
    }
    blob = " ".join(str(x) for x in (heading, subheading, terms_desc, terms) if x)
    mov = MOV_RE.search(blob or "")
    if mov:
        flat["minimumOrderValue"] = _parse_amount(mov.group(1))
    return flat


def _widgets_to_offers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    layout = payload.get("pageLayout")
    if not isinstance(layout, dict):
        return []
    widgets = layout.get("widgets")
    if not isinstance(widgets, list):
        return []
    cards: list[dict[str, Any]] = []
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        name = str(widget.get("widgetName") or "")
        data = widget.get("data")
        if not isinstance(data, dict):
            continue
        items = data.get("items")
        if "COUPON_CARD" not in name or not isinstance(items, dict):
            continue
        meta = _dig(items, "couponButton.action.actionMeta") or {}
        if isinstance(meta, dict) and (
            meta.get("couponCode") or meta.get("couponType") or items.get("couponCode")
        ):
            cards.append(_flatten_coupon_card(items))
    return _dedupe_offers(cards)


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Widgets (live) or sections[].coupons / coupons (fixture)."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return _dedupe_offers([x for x in payload if isinstance(x, dict)])
    if not isinstance(payload, dict):
        return []

    widget_cards = _widgets_to_offers(payload)
    if widget_cards:
        return widget_cards

    sections = payload.get("sections")
    if isinstance(sections, list):
        items: list[dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            chunk = section.get("coupons")
            if isinstance(chunk, list):
                items.extend(x for x in chunk if isinstance(x, dict))
        if items:
            return _dedupe_offers(items)

    coupons = payload.get("coupons")
    if isinstance(coupons, list) and coupons:
        return _dedupe_offers([x for x in coupons if isinstance(x, dict)])

    return []


def _parse_discount(text: str | None, explicit: dict[str, Any] | None = None) -> Discount:
    explicit = explicit or {}
    raw = text or explicit.get("raw")
    dtype = str(explicit.get("type") or explicit.get("discountType") or "").lower()
    value = _parse_amount(explicit.get("value") or explicit.get("discountAmount") or explicit.get("amount"))
    max_cap_f = _parse_amount(explicit.get("max_cap") or explicit.get("maxDiscount"))

    if raw and (value is None or dtype in ("", "unknown")):
        pct = PERCENT_RE.search(raw)
        if pct:
            return Discount(
                type="percent",
                value=_parse_amount(pct.group(1)),
                max_cap=_parse_amount(pct.group(2)) if pct.group(2) else max_cap_f,
                raw=raw,
            )
        if re.search(r"cashback", raw or "", re.I):
            flat = FLAT_RE.search(raw)
            return Discount(
                type="cashback",
                value=_parse_amount(flat.group(1)) if flat else value,
                max_cap=max_cap_f,
                raw=raw,
            )
        flat = FLAT_RE.search(raw)
        if flat and ("flat" in raw.lower() or "save" in raw.lower() or "off" in raw.lower() or "₹" in raw):
            return Discount(
                type="flat",
                value=_parse_amount(flat.group(1)),
                max_cap=max_cap_f,
                raw=raw,
            )

    if "percent" in dtype or dtype in {"percentage", "pct"}:
        return Discount(type="percent", value=value, max_cap=max_cap_f, raw=raw)
    if "cashback" in dtype:
        return Discount(type="cashback", value=value, max_cap=max_cap_f, raw=raw)
    if value is not None:
        return Discount(type="flat", value=value, max_cap=max_cap_f, raw=raw)
    return Discount(type="unknown", value=None, raw=raw)


def _status_from(item: dict[str, Any], blob: str) -> OfferStatus:
    status = str(item.get("status") or "").lower()
    if status in {"unlocked", "eligible", "applicable", "available"} or status.startswith("unlock"):
        return "unlocked"
    if "ineligible" in status or status in {"locked", "lock"} or re.search(r"\blocked\b", blob.lower()):
        return "locked"
    if "lock" in status and "unlock" not in status:
        return "locked"
    if SHOP_MORE_RE.search(blob):
        return "locked"
    return "unknown"


def _usable_bank_label(label: str) -> bool:
    s = label.strip()
    if not s or len(s) > 80:
        return False
    if re.search(r"payment\s*logos?|untitled|unknown", s, re.I):
        return False
    if re.search(r"\b(flat|save|get|upto|up to)\b.*[₹rs%].*\boff\b|\bcashback\b", s, re.I):
        return False
    return True


def _extract_bank_phrase(text: str) -> str | None:
    patterns = (
        r"with\s+([A-Za-z][A-Za-z0-9 &.+'-]{1,50}?(?:Bank|Visa|Mastercard|Master Card|RuPay|Amex|Credit Cards?|Debit Cards?)[^.,;]*)",
        r"on\s+([A-Za-z][A-Za-z0-9 &.+'-]{1,50}?(?:Bank|Visa|Mastercard|RuPay|Credit Cards?|Debit Cards?)[^.,;]*)",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            phrase = re.sub(r"\s+", " ", m.group(1)).strip(" -")
            if (CARD_KEYWORDS.search(phrase) or BANK_KEYWORDS.search(phrase)) and _usable_bank_label(phrase):
                return phrase
    return None


def _bank_name(item: dict[str, Any], title: str, description: str) -> str:
    for key in ("bankName", "cardName", "partnerName"):
        val = item.get(key)
        if val and _usable_bank_label(str(val)):
            return str(val).strip()
    for candidate in (item.get("subtitle"), description, title):
        if not candidate:
            continue
        text = str(candidate).strip()
        extracted = _extract_bank_phrase(text)
        if extracted:
            return extracted
        if BANK_KEYWORDS.search(text) and _usable_bank_label(text):
            return text
    return "Unknown bank/card"


def normalize_offer(item: dict[str, Any]) -> Offer:
    title = str(item.get("title") or item.get("name") or "Untitled offer")
    description = str(item.get("description") or item.get("subtitle") or item.get("subTitle") or "")
    blob = " ".join(
        str(x)
        for x in (title, description, item.get("couponCode"), item.get("promoCode"), item.get("offerType"), item.get("bankName"))
        if x
    )

    promo = item.get("couponCode") or item.get("promoCode")
    mov = item.get("minimumOrderValue") or item.get("minOrderValue")
    if mov is None:
        m = MOV_RE.search(blob)
        if m:
            mov = _parse_amount(m.group(1))
    else:
        mov = _parse_amount(mov)

    unlock = None
    more = SHOP_MORE_RE.search(blob)
    if more:
        amt = _parse_amount(more.group(1))
        unlock = f"Shop for ₹{amt:g} more to unlock" if amt is not None else None

    offer_type = str(item.get("offerType") or item.get("couponType") or item.get("type") or "").upper() or None

    discount_text = title if re.search(r"%|₹|rs|off|cashback|flat", title, re.I) else description
    discount = _parse_discount(
        discount_text,
        {
            "type": item.get("discountType"),
            "value": item.get("discountAmount") or item.get("amount"),
            "maxDiscount": item.get("maxDiscount"),
        },
    )

    terms = item.get("terms") or item.get("tnc")
    if isinstance(terms, list):
        terms = " | ".join(str(t) for t in terms)

    return Offer(
        title=title.strip(),
        bank_or_card=_bank_name(item, title, description),
        discount=discount,
        promo_code=str(promo).strip() if promo else None,
        min_order_value=mov,
        max_discount=discount.max_cap,
        status=_status_from(item, blob),
        offer_type=offer_type,
        terms=str(terms) if terms else None,
        unlock_message=unlock,
        raw_id=str(item.get("id") or item.get("couponId") or "") or None,
    )


def extract_raw_offers(payload: Any) -> list[dict[str, Any]]:
    return _as_list(payload)


def normalize_offers(payload: Any) -> list[Offer]:
    return [normalize_offer(item) for item in extract_raw_offers(payload)]
