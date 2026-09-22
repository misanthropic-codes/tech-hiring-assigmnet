from zepto_offers.filter import filter_bank_offers, is_bank_or_card_offer
from zepto_offers.normalize import normalize_offers, _parse_discount
from zepto_offers.models import Offer, Discount
import json
from pathlib import Path

payload = json.loads(Path('fixtures/sample_fetch_list.json').read_text())
offers = normalize_offers(payload)
kept, excluded = filter_bank_offers(offers, payload['sections'][0]['coupons'])
assert len(kept) == 3, kept
assert excluded == 3
codes = {o.promo_code for o in kept}
assert codes == {'ZEPIDFCCC', 'ZEPHSBC', 'ZEPMAHDFCDC'}
banks = {o.bank_or_card for o in kept}
assert 'IDFC FIRST Bank' in banks and 'HSBC' in banks and 'HDFC Mastercard' in banks

# Assessment rule: UPI / wallet labeled BANK_OFFER must be excluded
upi = Offer(
    title='Get ₹75 cashback with BHIM App',
    bank_or_card='Unknown bank/card',
    discount=Discount(type='cashback', value=75),
    promo_code='ZEPBHIM',
    offer_type='BANK_OFFER',
)
assert is_bank_or_card_offer(upi) is False

# RuPay credit card via app should still keep
rupay = Offer(
    title='Get flat ₹50 Cashback on RuPay Credit Card Payments via BHIM',
    bank_or_card='RuPay Credit Card',
    discount=Discount(type='cashback', value=50),
    promo_code='BHIMRUPAY',
    offer_type='BANK_OFFER',
)
assert is_bank_or_card_offer(rupay) is True

# Indian comma amounts
d = _parse_discount('Flat ₹6,000 Off with SBI Credit Cards')
assert d.value == 6000.0, d

print('OK', [o.model_dump() for o in kept])
