import tempfile
import unittest
from pathlib import Path

from csfauto.models import DeliveryOrder
from csfauto.state import StateStore


class StateTests(unittest.TestCase):
    def test_offer_id_survives_observation_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.db")
            order = DeliveryOrder(
                trade_id="1",
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
            store.observe(order)
            store.transition("1", "offer_created", offer_id=999)
            store.observe(order)
            state = store.get("1")
            self.assertEqual(state.offer_id, 999)
            self.assertEqual(state.status, "offer_created")
            store.close()


if __name__ == "__main__":
    unittest.main()

