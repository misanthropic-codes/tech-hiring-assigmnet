from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


DiscountType = Literal["flat", "percent", "cashback", "unknown"]
OfferStatus = Literal["locked", "unlocked", "unknown"]


class Discount(BaseModel):
    type: DiscountType = "unknown"
    value: float | None = None
    currency: str = "INR"
    raw: str | None = None
    max_cap: float | None = None


class StoreContext(BaseModel):
    id: str | None = None
    lat: float | None = None
    lng: float | None = None
    name: str | None = None


class Offer(BaseModel):
    title: str
    bank_or_card: str
    discount: Discount
    promo_code: str | None = None
    min_order_value: float | None = None
    max_discount: float | None = None
    status: OfferStatus = "unknown"
    offer_type: str | None = None
    terms: str | None = None
    unlock_message: str | None = None
    raw_id: str | None = None


class OfferReport(BaseModel):
    source: str = "zepto_payment_offers"
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    mode: Literal["live", "fixture"] = "live"
    store: StoreContext = Field(default_factory=StoreContext)
    offers: list[Offer] = Field(default_factory=list)
    excluded_count: int = 0
    notes: list[str] = Field(default_factory=list)
    raw_meta: dict[str, Any] = Field(default_factory=dict)
