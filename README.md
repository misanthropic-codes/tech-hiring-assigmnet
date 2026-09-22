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

## Prerequisites (do this before a live run)

Live mode opens **real Chrome** against [www.zepto.com](https://www.zepto.com). Payment Offers are **login-gated** and store-specific.

1. **Python 3.10+** and Google Chrome installed on the machine.
2. Complete **Setup** below (venv, deps, Playwright browsers).
3. **Log in to Zepto** at least once (phone + OTP) so the script can save a session:
   - Either run `--login` (recommended), **or**
   - Use `--live --wait-login --phone <number>` and finish OTP in the opened window.
4. **Set a delivery address / location** (e.g. Whitefield, Bengaluru) so a store is selected. Offers and MOV/lock state depend on store + cart.
5. Prefer a **headed** run (default). Headless often fails AWS WAF.
6. Keep secrets out of git: `.env` and `storage_state.json` are gitignored.

Optional: pin location in `.env`:

```bash
ZEPTO_LAT=12.96902
ZEPTO_LNG=77.75395
ZEPTO_STORAGE_STATE=storage_state.json
ZEPTO_HEADED=1
```

## Setup

```bash
cd tech-hiring-assigmnet   # or your clone path
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest          # only needed for tests
playwright install chrome
# if chrome channel unavailable:
playwright install chromium
cp .env.example .env
```

## Running commands

Activate the venv first every time:

```bash
source .venv/bin/activate
```

### 1) Offline demo (no Zepto login)

```bash
PYTHONPATH=src python -m zepto_offers --fixture --out output/offers.json
```

### 2) One-time Zepto login (phone + OTP in Chrome)

```bash
PYTHONPATH=src python -m zepto_offers --login --phone 9523035004
```

- Completes login in the browser window (enter OTP when asked).
- Writes `storage_state.json` (gitignored).
- Skips OTP automatically if auth cookies already exist.

### 3) Live extract (normal run)

```bash
PYTHONPATH=src python -m zepto_offers --live --out output/offers.json
```

Prints a readable offer list to the terminal, then full JSON, and writes `output/offers.json`.

### 4) Live extract when session expired / first time

```bash
PYTHONPATH=src python -m zepto_offers --live --wait-login --phone 9523035004 --out output/offers.json
```

Enter OTP in Chrome if prompted. Optional location override:

```bash
PYTHONPATH=src python -m zepto_offers --live --lat 12.96902 --lng 77.75395 --out output/offers.json
```

## Debugging commands

### Check last saved offers quickly

```bash
python3 -c "import json; d=json.load(open('output/offers.json')); print(len(d.get('offers',[])), 'offers'); print([o.get('promo_code') for o in d.get('offers',[])[:10]])"
```

### Inspect raw intercepted API payloads

```bash
python3 -c "import json,os; p='output/raw_coupon_responses.json'; print('exists', os.path.exists(p), 'bytes', os.path.getsize(p) if os.path.exists(p) else 0); raw=json.load(open(p)); print('responses', len(raw)); print(raw[0]['url'] if raw else 'empty')"
```

### Re-normalize + filter from a saved raw capture (no browser)

Useful when live UI clicked wrong but `raw_coupon_responses.json` still has `fetch-list`:

```bash
PYTHONPATH=src python - <<'PY'
import json
from pathlib import Path
from zepto_offers.filter import filter_bank_offers
from zepto_offers.normalize import extract_raw_offers, normalize_offers
raw = json.loads(Path('output/raw_coupon_responses.json').read_text())
payload = max(
    (r['payload'] for r in raw if isinstance(r.get('payload'), dict)),
    key=lambda p: json.dumps(p).upper().count('BANK_OFFER'),
    default=None,
)
assert payload, 'no usable payload in raw file'
offers = normalize_offers(payload)
kept, excluded = filter_bank_offers(offers, extract_raw_offers(payload))
print(f'kept {len(kept)} excluded {excluded}')
for o in kept[:15]:
    print(o.promo_code, '|', o.title, '|', o.bank_or_card)
PY
```

### Unit / edge-case tests

```bash
PYTHONPATH=src python -m pytest tests/test_edge_cases.py tests/test_filter_fixture.py -v
```

### Common failure: “No coupon payload captured”

| Likely cause | What to do |
|---|---|
| Not logged in / session expired | `--login --phone …` or `--live --wait-login --phone …` |
| No delivery location / store | Set address on Zepto (or `--lat` / `--lng`) |
| Coupons UI not opened | Check `artifacts/debug_no_coupons.png` if present; retry `--live` |
| Empty `output/offers.json` after a failed run | Re-run live; failed runs no longer wipe a good `raw_*.json` when capture is empty |
| Editor shows 1 empty line | Reload file from disk (content is on disk) |

Confirm session file exists:

```bash
ls -la storage_state.json
python3 -c "import json; c=json.load(open('storage_state.json')).get('cookies',[]); print([x['name'] for x in c if x['name'] in ('isAuth','accessToken','user_id')])"
```

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
output/               Live offers.json + raw_coupon_responses.json (gitignored)
artifacts/            Debug screenshots when capture fails
docs/architecture.md  System design + sequence
docs/research-notes.md  Live reverse-engineering notes
tests/                Fixture + edge-case tests
```

## Interview extension points

- Replay `fetch-list` with signed headers once signature algorithm is known.
- Schema-validate payloads; alert when Zepto renames fields.
- Cache per `store_id`; diff offers over time.
- Mobile app traffic as a second source if web auth changes.
