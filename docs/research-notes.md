# Zepto Payment Offers — Research Notes

## Site
- Canonical web app: `https://www.zepto.com` (also `zeptonow.com` → redirects)
- Protected by **AWS WAF** (`x-amzn-waf-action: challenge`). Headless Chromium alone often gets HTTP 202/429.
- Real Chrome (`channel="chrome"`) + headed mode reliably passes WAF.

## BFF
- Gateway: `https://bff-gateway.zepto.com`
- Home/layout: `GET /lms/api/v2/get_page?latitude=&longitude=&page_type=HOME...`
- Required client headers observed: `store_id` / `storeid`, `store_ids`, `session_id`, `device_id`, `compatible_components`, `platform=WEB`, `app_version`, `x-csrf-secret`, `x-xsrf-token`, `request-signature`

## Coupons / Payment Offers (from JS bundle reverse-eng)
Cart coupon endpoints (`cfs` = cart fulfillment service):

| Constant | Path |
|---|---|
| CART | `cfs/api/v1/cart` |
| CART_CREATE | `cfs/api/v1/cart/create` |
| CART_COUPONS | `cfs/api/v1/cart/coupons` |
| CART_COUPONS_APPLY | `cfs/api/v1/cart/coupons/apply` |
| CART_COUPONS_REMOVE | `cfs/api/v1/cart/coupons/remove` |
| **CART_COUPONS_FETCH_LIST** | **`cfs/api/v1/cart/coupons/fetch-list`** |

Offer type enum in client:

- `DELIVERY`
- `PRODUCT`
- `WALLET_OFFER`
- **`BANK_OFFER`** ← target filter
- `AUTO_ELIGIBLE_PAYMENT_OFFER`
- `PROMO_CASH`

## Auth gate ( empirically confirmed)
Guest cart shows **"Login to view coupons"** / **"Login to Proceed"**.
Coupon list APIs are not fired until the user is authenticated.
Cold HTTP replay of `fetch-list` without a valid signed browser session returns **429**.

## Implication for architecture
1. Use Playwright (Chrome) as session bootstrap + network interceptor.
2. Prefer intercepting `**/cfs/api/v1/cart/coupons/fetch-list` over DOM scraping.
3. Persist `storage_state.json` after one interactive login (OTP) so subsequent runs are non-interactive.
4. Filter to `BANK_OFFER` (and card-like instruments); exclude `WALLET_OFFER`, `DELIVERY`, `PRODUCT`, `PROMO_CASH`.
5. Fixture mode for offline/CI demos when live login is unavailable.
