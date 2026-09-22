"""Adversarial / worst-case tests for filter, normalize, cookies, and CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zepto_offers.browser import (  # noqa: E402
    _is_authenticated_from_cookies,
    is_fetch_list_url,
)
from zepto_offers.cli import main  # noqa: E402
from zepto_offers.filter import filter_bank_offers, is_bank_or_card_offer  # noqa: E402
from zepto_offers.models import Discount, Offer  # noqa: E402
from zepto_offers.normalize import (  # noqa: E402
    _parse_amount,
    _parse_discount,
    extract_raw_offers,
    normalize_offer,
    normalize_offers,
)


def _offer(**kwargs) -> Offer:
    defaults = dict(
        title="Untitled",
        bank_or_card="Unknown bank/card",
        discount=Discount(type="unknown"),
        offer_type="BANK_OFFER",
    )
    defaults.update(kwargs)
    return Offer(**defaults)


# ---------------------------------------------------------------------------
# Filter — assessment edge cases
# ---------------------------------------------------------------------------


class TestFilterEdgeCases:
    def test_empty_offer_list(self):
        kept, excluded = filter_bank_offers([])
        assert kept == [] and excluded == 0

    def test_mismatched_raw_lengths_does_not_crash(self):
        offers = [_offer(title="Flat ₹100 Off", bank_or_card="HDFC Bank", promo_code="X")]
        kept, excluded = filter_bank_offers(offers, [])  # shorter raw list
        assert len(kept) + excluded == 1

    def test_wallet_labeled_bank_offer_excluded(self):
        assert is_bank_or_card_offer(
            _offer(title="₹50 Amazon Pay cashback", promo_code="AMZPAY50")
        ) is False

    def test_upi_code_without_card_excluded(self):
        assert is_bank_or_card_offer(
            _offer(title="Save ₹25!", bank_or_card="au small finance", promo_code="AUUPI")
        ) is False

    def test_bhim_app_excluded(self):
        assert is_bank_or_card_offer(
            _offer(title="Cashback with BHIM app", promo_code="ZEPBHIM")
        ) is False

    def test_delivery_type_excluded(self):
        assert is_bank_or_card_offer(_offer(offer_type="DELIVERY", title="Free delivery")) is False

    def test_membership_type_excluded(self):
        assert is_bank_or_card_offer(_offer(offer_type="MEMBERSHIP", title="Zepto Pass")) is False

    def test_bare_bank_offer_type_without_bank_text_excluded(self):
        assert is_bank_or_card_offer(
            _offer(title="Special deal", bank_or_card="Unknown bank/card", offer_type="BANK_OFFER")
        ) is False

    def test_rupay_credit_card_via_app_kept(self):
        assert is_bank_or_card_offer(
            _offer(
                title="Cashback on RuPay Credit Card via BHIM",
                bank_or_card="RuPay Credit Card",
                promo_code="BHIMRUPAY",
            )
        ) is True

    def test_idfc_credit_card_kept(self):
        assert is_bank_or_card_offer(
            _offer(
                title="Flat ₹125 Off",
                bank_or_card="IDFC FIRST Bank",
                promo_code="ZEPIDFCCC",
            )
        ) is True

    def test_app_only_with_bank_name_excluded(self):
        assert is_bank_or_card_offer(
            _offer(
                title="Save ₹25!",
                bank_or_card="au small finance",
                promo_code="AUAPP",
                terms="Valid only on Payment made by AU 0101 App",
            )
        ) is False

    def test_unicode_garbage_title_does_not_crash(self):
        assert is_bank_or_card_offer(_offer(title="💥🔥\x00\uffff", promo_code=None)) is False

    def test_none_like_fields(self):
        o = _offer(title="", bank_or_card="", promo_code=None, terms=None, offer_type=None)
        assert is_bank_or_card_offer(o) is False


# ---------------------------------------------------------------------------
# Normalize — malformed payloads
# ---------------------------------------------------------------------------


class TestNormalizeEdgeCases:
    def test_none_payload(self):
        assert extract_raw_offers(None) == []
        assert normalize_offers(None) == []

    def test_empty_dict(self):
        assert extract_raw_offers({}) == []
        assert normalize_offers({}) == []

    def test_empty_list(self):
        assert extract_raw_offers([]) == []

    def test_string_payload(self):
        assert extract_raw_offers("not-json-object") == []

    def test_deeply_nested_noise(self):
        payload = {"a": {"b": [{"c": 1}, {"d": {"e": []}}]}, "f": None}
        assert extract_raw_offers(payload) == []

    def test_indian_comma_amounts(self):
        d = _parse_discount("Flat ₹6,000 Off with SBI Credit Cards")
        assert d.value == 6000.0
        d2 = _parse_discount("Flat ₹1,500 Off")
        assert d2.value == 1500.0

    def test_parse_amount_junk(self):
        assert _parse_amount(None) is None
        assert _parse_amount("") is None
        assert _parse_amount("abc") is None
        assert _parse_amount("1,25,000") == 125000.0  # if group matches oddly — may be 125000

    def test_percent_with_cap(self):
        d = _parse_discount("Get 15% up to ₹125 off")
        assert d.type == "percent"
        assert d.value == 15.0
        assert d.max_cap == 125.0

    def test_widget_card_missing_meta(self):
        payload = {
            "pageLayout": {
                "widgets": [
                    {
                        "widgetName": "COUPON_CARD_WIDGET_SECTION_1_1",
                        "data": {"items": {"heading": {"text": "Save ₹100!"}}},
                    }
                ]
            }
        }
        # No coupon meta → skipped by widget extractor; may still walk
        offers = normalize_offers(payload)
        assert isinstance(offers, list)

    def test_widget_happy_path(self):
        payload = {
            "pageLayout": {
                "widgets": [
                    {
                        "widgetName": "COUPON_CARD_WIDGET_SECTION_1_1",
                        "data": {
                            "items": {
                                "heading": {"text": "Save ₹125!"},
                                "subheading": {"text": "Flat ₹125 off with IDFC First Bank Credit Cards"},
                                "couponCode": {"text": "ZEPIDFCDC"},
                                "couponButton": {
                                    "couponState": "UNLOCKED",
                                    "action": {
                                        "actionMeta": {
                                            "couponCode": "ZEPIDFCDC",
                                            "couponId": "abc",
                                            "couponType": "BANK_OFFER",
                                        }
                                    },
                                },
                                "termsAndConditions": {
                                    "description": "Valid on orders above ₹599",
                                    "terms": ["once per card"],
                                },
                            }
                        },
                    }
                ]
            }
        }
        offers = normalize_offers(payload)
        assert len(offers) == 1
        o = offers[0]
        assert o.promo_code == "ZEPIDFCDC"
        assert o.discount.value == 125.0
        assert o.status == "unlocked"
        assert o.min_order_value == 599.0
        kept, excluded = filter_bank_offers(offers)
        assert len(kept) == 1 and excluded == 0

    def test_unlocked_not_misclassified_as_locked(self):
        o = normalize_offer(
            {
                "title": "Save ₹100!",
                "subtitle": "HDFC Bank Credit Cards",
                "couponCode": "X",
                "offerType": "BANK_OFFER",
                "status": "UNLOCKED",
            }
        )
        assert o.status == "unlocked"

    def test_duplicate_coupons_deduped(self):
        payload = {
            "coupons": [
                {"id": "1", "title": "A", "couponCode": "SAME", "offerType": "BANK_OFFER", "bankName": "SBI"},
                {"id": "1", "title": "A again", "couponCode": "SAME", "offerType": "BANK_OFFER", "bankName": "SBI"},
            ]
        }
        raw = extract_raw_offers(payload)
        assert len(raw) == 1


# ---------------------------------------------------------------------------
# Cookie auth edge cases
# ---------------------------------------------------------------------------


class TestCookieAuthEdgeCases:
    def test_empty_cookies(self):
        assert _is_authenticated_from_cookies([]) is False

    def test_waf_only(self):
        assert (
            _is_authenticated_from_cookies(
                [{"name": "aws-waf-token", "value": "x"}, {"name": "XSRF-TOKEN", "value": "y"}]
            )
            is False
        )

    def test_is_auth_false(self):
        assert (
            _is_authenticated_from_cookies(
                [
                    {"name": "isAuth", "value": "false"},
                    {"name": "accessToken", "value": "eyJhbGciOiJIUzUxMiJ9." + "a" * 40},
                    {"name": "user_id", "value": "38830c19-c731-44d7-a0e8-f1e053084d0d"},
                ]
            )
            is False
        )

    def test_valid_session(self):
        assert (
            _is_authenticated_from_cookies(
                [
                    {"name": "isAuth", "value": "true"},
                    {"name": "accessToken", "value": "eyJhbGciOiJIUzUxMiJ9." + "a" * 40},
                    {"name": "user_id", "value": "38830c19-c731-44d7-a0e8-f1e053084d0d"},
                ]
            )
            is True
        )

    def test_access_plus_refresh_without_is_auth(self):
        assert (
            _is_authenticated_from_cookies(
                [
                    {"name": "accessToken", "value": "eyJhbGciOiJIUzUxMiJ9." + "a" * 40},
                    {"name": "refreshToken", "value": "322f393f-d242-4c1b-881f-e200e17fd8cf"},
                ]
            )
            is True
        )

    def test_short_token_rejected(self):
        assert (
            _is_authenticated_from_cookies(
                [
                    {"name": "accessToken", "value": "short"},
                    {"name": "refreshToken", "value": "r"},
                ]
            )
            is False
        )


# ---------------------------------------------------------------------------
# URL helper
# ---------------------------------------------------------------------------


class TestUrlHelpers:
    def test_fetch_list_url(self):
        assert is_fetch_list_url(
            "https://bff-gateway.zepto.com/cfs/api/v1/cart/coupons/fetch-list?x=1"
        )
        assert not is_fetch_list_url("https://bff-gateway.zepto.com/cfs/api/v1/cart")


# ---------------------------------------------------------------------------
# CLI — fixture / bad input
# ---------------------------------------------------------------------------


class TestCliEdgeCases:
    def test_fixture_mode_ok(self, tmp_path, capsys):
        out = tmp_path / "out.json"
        code = main(["--fixture", "--out", str(out)])
        assert code == 0
        data = json.loads(out.read_text())
        assert data["mode"] == "fixture"
        assert len(data["offers"]) == 3
        captured = capsys.readouterr()
        assert "Zepto Payment Offers" in captured.out

    def test_missing_fixture_file(self, tmp_path):
        missing = tmp_path / "nope.json"
        with pytest.raises(FileNotFoundError):
            main(["--fixture", str(missing)])

    def test_corrupt_json_fixture(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            main(["--fixture", str(bad)])

    def test_empty_json_object_fixture(self, tmp_path, capsys):
        empty = tmp_path / "empty.json"
        empty.write_text("{}", encoding="utf-8")
        out = tmp_path / "out.json"
        code = main(["--fixture", str(empty), "--out", str(out)])
        assert code == 0
        data = json.loads(out.read_text())
        assert data["offers"] == []
        assert "no bank/card offers" in capsys.readouterr().out.lower() or data["excluded_count"] == 0


# ---------------------------------------------------------------------------
# Pipeline — live-shaped widget dump filtered end-to-end
# ---------------------------------------------------------------------------


class TestLiveShapedPipeline:
    def test_mixed_bank_and_upi_widgets(self):
        payload = {
            "pageLayout": {
                "widgets": [
                    {
                        "widgetName": "COUPON_CARD_WIDGET_SECTION_1_1",
                        "data": {
                            "items": {
                                "heading": {"text": "Save ₹100!"},
                                "subheading": {"text": "HDFC Bank Credit Cards"},
                                "couponCode": {"text": "ZEPHDFC"},
                                "couponButton": {
                                    "couponState": "UNLOCKED",
                                    "action": {
                                        "actionMeta": {
                                            "couponCode": "ZEPHDFC",
                                            "couponId": "1",
                                            "couponType": "BANK_OFFER",
                                        }
                                    },
                                },
                                "termsAndConditions": {"description": "orders above ₹499", "terms": []},
                            }
                        },
                    },
                    {
                        "widgetName": "COUPON_CARD_WIDGET_SECTION_1_2",
                        "data": {
                            "items": {
                                "heading": {"text": "Get ₹50 on Amazon Pay"},
                                "subheading": {"text": "Amazon Pay wallet"},
                                "couponCode": {"text": "AMZPAY50"},
                                "couponButton": {
                                    "couponState": "UNLOCKED",
                                    "action": {
                                        "actionMeta": {
                                            "couponCode": "AMZPAY50",
                                            "couponId": "2",
                                            "couponType": "BANK_OFFER",
                                        }
                                    },
                                },
                                "termsAndConditions": {"description": "orders above ₹99", "terms": []},
                            }
                        },
                    },
                ]
            }
        }
        offers = normalize_offers(payload)
        kept, excluded = filter_bank_offers(offers, extract_raw_offers(payload))
        assert len(offers) == 2
        assert len(kept) == 1
        assert excluded == 1
        assert kept[0].promo_code == "ZEPHDFC"
