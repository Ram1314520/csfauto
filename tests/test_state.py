import tempfile
import unittest
from pathlib import Path

from csfauto.models import DeliveryOrder
from csfauto.state import StateStore


class StateTests(unittest.TestCase):
    @staticmethod
    def _order(trade_id: str = "1") -> DeliveryOrder:
        return DeliveryOrder(
            trade_id=trade_id,
            state="pending",
            seller_id=76561198886468290,
            buyer_id=76561199509974349,
            asset_ids=(100,),
            accepted_at="now",
            trade_url="https://steamcommunity.com/tradeoffer/new/?partner=1&token=x",
            trade_token="x",
            wait_for_cancel_ping=False,
            steam_offer_id=None,
            steam_offer_state=None,
            item_name="item",
        )

    def test_only_session_expired_manual_reviews_are_released(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.db")
            session_order = self._order("session")
            unsafe_order = self._order("unsafe")
            store.observe(session_order)
            store.observe(unsafe_order)
            store.transition(
                "session",
                "manual_review",
                error="SessionExpired: details redacted",
                increment_attempts=True,
            )
            store.transition(
                "unsafe",
                "manual_review",
                error="Steam报价给出资产与CSFloat订单不完全一致",
                increment_attempts=True,
            )
            self.assertEqual(store.release_session_expired_reviews(), 1)
            self.assertEqual(store.get("session").status, "retryable_error")
            self.assertEqual(store.get("session").attempts, 0)
            self.assertEqual(store.get("unsafe").status, "manual_review")
            store.close()

    def test_offer_id_survives_observation_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.db")
            order = self._order()
            store.observe(order)
            store.transition("1", "offer_created", offer_id=999)
            store.observe(order)
            state = store.get("1")
            self.assertEqual(state.offer_id, 999)
            self.assertEqual(state.status, "offer_created")
            store.close()


if __name__ == "__main__":
    unittest.main()

