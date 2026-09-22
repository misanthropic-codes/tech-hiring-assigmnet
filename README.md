# Zepto Payment Offers Extractor

Python CLI that extracts **bank / credit / debit card payment offers** from Zepto checkout
(`Coupon & Offers` → **Payment Offers**) and prints a clean list to the terminal (plus optional JSON).

Built for the [Startup Tech Hiring take-home](https://docs.google.com/forms/d/e/1FAIpQLSf_iCCrzKtViQI3ZYAMfoh_pv3kIcpZTqO66JARwPVzykXZXA/viewform):
outcome + code quality + reasoning matter more than a prescribed technique.

## What it outputs

For each offer (bank / card only):

- title / short description
- bank or card name
- discount (`flat` / `percent` / `cashback`)
- promo code, min order value, max discount cap, lock status, terms (when present)

**Filter (per brief):** delivery, wallet, UPI-app, membership, and generic promo offers are excluded —
even when Zepto’s API labels them `BANK_OFFER`. RuPay / Visa / Mastercard credit–debit offers are kept.

## Approach (short)

**Network-first, browser-bootstrap** — not DOM scraping.

1. Playwright opens Zepto in real Chrome (AWS WAF blocks naïve headless/curl).
2. Reuses auth cookies from `storage_state.json` when already logged in (skips OTP).
3. After cart → Payment Offers, intercepts `bff-gateway.zepto.com` → `cfs/api/v1/cart/coupons/fetch-list`.
4. Normalizes Zepto’s `pageLayout` coupon-card widgets and filters to bank/card offers.
5. Offline `--fixture` mode demos the pipeline using a sample shaped like the assessment screenshot.

See [docs/architecture.md](docs/architecture.md) and [docs/research-notes.md](docs/research-notes.md).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chrome
# if chrome channel unavailable:
playwright install chromium
cp .env.example .env
```

## Usage

### Offline demo (no Zepto login)

```bash
PYTHONPATH=src python -m zepto_offers --fixture --out output/offers.json
```

### One-time login (OTP in browser)

```bash
PYTHONPATH=src python -m zepto_offers --login --phone <10-digit>
# writes storage_state.json (gitignored). Skips OTP if auth cookies already exist.
```

### Live extract

```bash
PYTHONPATH=src python -m zepto_offers --live --out output/offers.json
# session expired / first time:
PYTHONPATH=src python -m zepto_offers --live --wait-login --phone <10-digit> --out output/offers.json
```

Prints a readable offer list to the terminal, then full JSON (and writes `--out` if set).

## Example output

```text
=== Zepto Payment Offers (3 kept, 3 excluded) ===
  1. [locked] Flat ₹125 Off | IDFC FIRST Bank | ₹125 | ZEPIDFCCC | MOV ₹599
  ...
```

```json
{
  "source": "zepto_payment_offers",
  "mode": "fixture",
  "offers": [
    {
      "title": "Flat ₹125 Off",
      "bank_or_card": "IDFC FIRST Bank",
      "discount": { "type": "flat", "value": 125, "currency": "INR" },
      "promo_code": "ZEPIDFCCC",
      "min_order_value": 599,
      "status": "locked",
      "offer_type": "BANK_OFFER"
    }
  ],
  "excluded_count": 3
}
```

## Assumptions

- **Login is required** for Payment Offers on web (guest cart shows “Login to view coupons”).
- Offer eligibility / lock state depends on **store + cart value**; pin lat/lng via env/`--lat`/`--lng`.
- Live runs should use **headed Chrome**; headless often hits WAF 202/429.
- Secrets/session stay in `.env` / `storage_state.json` (gitignored) — never commit them.
- Zepto’s `BANK_OFFER` enum is broader than the brief; we additionally require bank/card wording.

## Project layout

```text
src/zepto_offers/     CLI + browser intercept + normalize/filter
fixtures/             Offline sample (assessment-like bank offers)
docs/architecture.md  System design + sequence
docs/research-notes.md  Live reverse-engineering notes
```

## Interview extension points

- Replay `fetch-list` with signed headers once signature algorithm is known.
- Schema-validate payloads; alert when Zepto renames fields.
- Cache per `store_id`; diff offers over time.
- Mobile app traffic as a second source if web auth changes.
