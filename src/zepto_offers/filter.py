from __future__ import annotations

import re

from .models import Offer
from .normalize import (
    BANK_KEYWORDS,
    CARD_KEYWORDS,
    NON_BANK_KEYWORDS,
    NON_BANK_TYPES,
)


def _offer_blob(offer: Offer, raw: dict | None = None) -> str:
    raw = raw or {}
    parts: list[str] = []
    for x in (
        offer.title,
        offer.bank_or_card,
        offer.promo_code,
        offer.terms,
        offer.unlock_message,
        raw.get("description"),
        raw.get("subtitle"),
        raw.get("subheading"),
        raw.get("bankName"),
        raw.get("instrumentName"),
        raw.get("offerType"),
        raw.get("couponType"),
    ):
        if not x:
            continue
        s = str(x).strip()
        # Placeholder labels must not satisfy BANK_KEYWORDS via the word "bank"
        if s.lower() in {"unknown bank/card", "unknown", "n/a", "untitled", "untitled offer"}:
            continue
        parts.append(s)
    return " ".join(parts)


def is_bank_or_card_offer(offer: Offer, raw: dict | None = None) -> bool:
    """Keep only offers tied to a bank or credit/debit card.

    Zepto often labels UPI / wallet / app cashback as ``BANK_OFFER`` — those are
    excluded per the take-home brief unless a card network is clearly named
    (e.g. RuPay Credit Card via an app).
    """
    raw = raw or {}
    type_hint = (offer.offer_type or str(raw.get("offerType") or raw.get("type") or "")).upper()
    blob = _offer_blob(offer, raw)

    if type_hint in NON_BANK_TYPES:
        return False

    has_card = bool(CARD_KEYWORDS.search(blob))
    has_bank = bool(BANK_KEYWORDS.search(blob))
    is_wallet_upi = bool(NON_BANK_KEYWORDS.search(blob))
    code = (offer.promo_code or "").upper()

    # Pure wallet / UPI / super-app instruments (even if type == BANK_OFFER)
    if is_wallet_upi and not has_card:
        return False

    # Promo codes like AUUPI / MBKUPI without a card network
    if re.search(r"UPI", code) and not has_card:
        return False

    # App-only cashback without a card instrument (e.g. AU 0101 app)
    if re.search(r"\bapp\b", blob, re.I) and not has_card:
        return False

    if has_card:
        return True

    # Named bank offer without wallet/UPI noise (IDFC / HSBC / IndusInd style)
    if has_bank and not is_wallet_upi:
        return True

    # Bare type labels (incl. BANK_OFFER) without bank/card copy are not enough
    return False


def filter_bank_offers(offers: list[Offer], raw_items: list[dict] | None = None) -> tuple[list[Offer], int]:
    raw_items = raw_items or [{}] * len(offers)
    kept: list[Offer] = []
    excluded = 0
    for offer, raw in zip(offers, raw_items, strict=False):
        if is_bank_or_card_offer(offer, raw if isinstance(raw, dict) else None):
            kept.append(offer)
        else:
            excluded += 1
    return kept, excluded
