# System Design — Zepto Bank/Card Payment Offers Extractor

## Goal

Extract **bank / credit / debit card payment offers** currently shown on Zepto checkout
("Coupon & Offers" → **Payment Offers**), and print a clean JSON list.

## Recommended approach (senior default)

**Network-first, browser-bootstrap.**

Do **not** scrape CSS cards as the source of truth. Zepto already loads offers as JSON from:

```text
POST/GET  https://bff-gateway.zepto.com/cfs/api/v1/cart/coupons/fetch-list
```

Filter client-side on offer type:

```text
BANK_OFFER   ✅ keep
WALLET_OFFER ❌ drop
DELIVERY     ❌ drop
PRODUCT      ❌ drop
PROMO_CASH   ❌ drop
```

## Why this beats DOM scraping

| Concern | DOM scrape | Network intercept |
|---|---|---|
| UI redesign | Breaks often | Stable field names |
| Filtering accuracy | Heuristic on labels | Typed `BANK_OFFER` enum |
| Speed | Slow | Fast once session exists |
| WAF / bot walls | Still need browser | Same browser bootstrap |


## Constraints discovered in research

1. **AWS WAF** challenges non-browser / naïve headless traffic.
2. Guest cart shows **Login to view coupons** — coupon APIs fire only after auth.
3. Cold HTTP replay without valid `request-signature` + browser cookies → **429**.
4. Store context (`store_id`, lat/lng) affects offer eligibility / lock state.

## High-level architecture

```text
┌──────────────┐   Chrome+WAF    ┌────────────────────┐
│ CLI / runner │ ──────────────► │ Session Bootstrap  │
│              │                 │ (Playwright)       │
└──────┬───────┘                 └─────────┬──────────┘
       │                                   │
       │                         set location, cart,
       │                         login (OTP once) /
       │                         reuse storage_state
       │                                   │
       │                                   ▼
       │                         ┌────────────────────┐
       │                         │ Network Interceptor│
       │                         │ fetch-list XHR     │
       │                         └─────────┬──────────┘
       │                                   │ raw JSON
       ▼                                   ▼
┌──────────────┐                 ┌────────────────────┐
│ Fixture mode │                 │ Normalizer         │
│ (offline)    │                 │ → Offer schema     │
└──────┬───────┘                 └─────────┬──────────┘
       │                                   │
       └──────────────┬────────────────────┘
                      ▼
             ┌────────────────────┐
             │ Bank/Card Filter   │
             │ (BANK_OFFER +      │
             │  keyword fallback) │
             └─────────┬──────────┘
                       ▼
             ┌────────────────────┐
             │ JSON stdout / file │
             └────────────────────┘
```

## Sequence (live path)

```text
User                CLI                 Playwright              Zepto BFF
 |                   |                      |                      |
 |-- run live ------>|                      |                      |
 |                   |-- launch Chrome ---->|                      |
 |                   |                      |-- GET / (WAF) ------>|
 |                   |                      |<-- page OK ----------|
 |                   |                      |-- get_page(lat,lng)->|
 |                   |                      |<-- store_id ---------|
 |                   |                      |-- add item / cart -->|
 |                   |                      |-- open coupons ----->|
 |  (OTP once)       |                      |-- login if needed -->|
 |<---- if needed ---|                      |                      |
 |                   |                      |-- fetch-list ------->|
 |                   |<-- intercepted JSON -|                      |
 |                   |-- normalize/filter -->|                      |
 |<-- JSON offers ---|                      |                      |
```

## Module breakdown

| Module | Responsibility |
|---|---|
| `cli.py` | Args: `--live` / `--fixture` / `--login` / `--save-state` |
| `browser.py` | Chrome launch, location, cart, coupons panel, intercept |
| `normalize.py` | Map raw coupon payload → stable `Offer` model |
| `filter.py` | Keep bank/card only |
| `models.py` | Pydantic schema for output contract |
| `fixtures/` | Offline sample for demos without OTP |

## Output contract

```json
{
  "source": "zepto_payment_offers",
  "fetched_at": "ISO-8601",
  "mode": "live|fixture",
  "store": {"id": "...", "lat": 0, "lng": 0},
  "offers": [
    {
      "title": "Flat ₹125 Off",
      "bank_or_card": "IDFC FIRST Bank",
      "discount": {"type": "flat", "value": 125, "currency": "INR", "raw": "Flat ₹125 Off"},
      "promo_code": "ZEPIDFCCC",
      "min_order_value": 599,
      "max_discount": null,
      "status": "locked",
      "offer_type": "BANK_OFFER",
      "terms": null
    }
  ],
  "excluded_count": 3
}
```

## Failure modes & mitigations

| Failure | Mitigation |
|---|---|
| WAF 202/429 | Real Chrome channel, headed, reuse cookies |
| Login required | Interactive `--login`, persist `storage_state.json` |
| Endpoint rename | Match URL substring `coupons/fetch-list`; fallback DOM parse of Payment Offers tab |
| Type field missing | Keyword allowlist (Bank, Visa, Mastercard, RuPay, …) + denylist (wallet, Amazon Pay, delivery) |
| Location drift | Pin lat/lng via env |

## What we intentionally do not build

- Full Zepto clone / product catalog scraper
- Payment execution
- Permanent credential storage in git
'''
)