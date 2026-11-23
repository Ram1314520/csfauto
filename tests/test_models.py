import unittest

from csfauto.models import DeliveryOrder, OrderValidationError, exact_asset_match
from csfauto.steam_client import MOBILECONF_USER_AGENT, _mobile_confirmation_headers, _sanitize_inventory_description


SELLER = 76561198886468290
BUYER = 76561199509974349
PARTNER = BUYER - 76561197960265728


def sample_order() -> dict:
    return {
        "id": "1044647390231006618",
        "state": "queued",
        "seller_id": str(SELLER),
        "buyer_id": str(BUYER),
        "accepted_at": None,
        "trade_url": f"https://steamcommunity.com/tradeoffer/new/?partner={PARTNER}&token=abc123",
        "trade_token": "abc123",
        "wait_for_cancel_ping": False,
        "steam_offer": {"is_from_seller": False},
        "contract": {"item": {"asset_id": "54584804108", "market_hash_name": "Revolution Case"}},
    }


class ModelTests(unittest.TestCase):
    def test_parses_and_validates_order(self):
        order = DeliveryOrder.from_api(sample_order())
        order.validate_for_delivery(SELLER)
        self.assertEqual(order.asset_ids, (54584804108,))

    def test_rejects_partner_mismatch(self):
        raw = sample_order()
        raw["trade_url"] = "https://steamcommunity.com/tradeoffer/new/?partner=1&token=abc123"
        order = DeliveryOrder.from_api(raw)
        with self.assertRaises(OrderValidationError):
            order.validate_for_delivery(SELLER)

    def test_asset_match_requires_full_multiset_equality(self):
        self.assertTrue(exact_asset_match([1, 2], [2, 1]))
        self.assertFalse(exact_asset_match([1], [1, 2]))
        self.assertFalse(exact_asset_match([1, 2, 3], [1, 2]))

    def test_accepted_steam_offer_is_completed(self):
        raw = sample_order()
        raw["state"] = "pending"
        raw["accepted_at"] = "2026-01-01T00:00:00Z"
        raw["steam_offer"] = {"id": "777", "state": 3}
        self.assertEqual(DeliveryOrder.from_api(raw).delivery_stage, "completed")

    def test_offer_waiting_for_mobile_confirmation_is_resumable(self):
        raw = sample_order()
        raw["state"] = "pending"
        raw["accepted_at"] = "2026-01-01T00:00:00Z"
        raw["steam_offer"] = {"id": "777", "state": 9}
        self.assertEqual(DeliveryOrder.from_api(raw).delivery_stage, "resume")

    def test_malformed_inspect_action_is_removed_without_mutating_input(self):
        raw = {
            "actions": [
                {"name": "Inspect in Game...", "link": "steam://broken-link"},
                {"name": "Use", "link": "steam://valid-other-action"},
            ]
        }
        sanitized = _sanitize_inventory_description(raw)
        self.assertEqual(sanitized["actions"], [{"name": "Use", "link": "steam://valid-other-action"}])
        self.assertEqual(len(raw["actions"]), 2)

    def test_mobile_confirmation_headers_are_temporary(self):
        headers = {"User-Agent": "desktop-agent", "Other": "value"}
        with _mobile_confirmation_headers(headers, "mobile-client"):
            self.assertEqual(headers["User-Agent"], MOBILECONF_USER_AGENT)
            self.assertEqual(headers["X-Requested-With"], "mobile-client")
        self.assertEqual(headers, {"User-Agent": "desktop-agent", "Other": "value"})


if __name__ == "__main__":
    unittest.main()

