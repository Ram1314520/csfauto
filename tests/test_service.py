import tempfile
import unittest
from pathlib import Path

from csfauto.config import Config
from csfauto.csfloat_api import CSFloatAPIError
from csfauto.service import DeliveryService
from csfauto.state import StateStore
from csfauto.steam_client import SteamRateLimitError


SELLER = 76561198886468290
BUYER = 76561199509974349
PARTNER = BUYER - 76561197960265728


def raw_order(*, accepted=False, offer_id=None, offer_state=2):
    return {
        "id": "1001",
        "state": "pending" if accepted else "queued",
        "seller_id": str(SELLER),
        "buyer_id": str(BUYER),
        "accepted_at": "2026-01-01T00:00:00Z" if accepted else None,
        "trade_url": f"https://steamcommunity.com/tradeoffer/new/?partner={PARTNER}&token=testtoken",
        "trade_token": "testtoken",
        "wait_for_cancel_ping": False,
        "steam_offer": {"id": str(offer_id), "state": offer_state} if offer_id else {"is_from_seller": False},
        "contract": {"item": {"asset_id": "501", "market_hash_name": "Test Item"}},
    }


def live_config(data_dir: Path) -> Config:
    return Config(
        mode="live",
        live_confirmation_phrase="I_UNDERSTAND_THIS_SENDS_REAL_STEAM_OFFERS",
        csfloat_api_key="x",
        steam_id64=SELLER,
        steam_login="user",
        steam_password="password",
        steam_api_key="key",
        shared_secret="secret",
        identity_secret="secret",
        poll_interval_seconds=300,
        request_timeout_seconds=30,
        max_retries=2,
        max_order_attempts=5,
        confirmation_attempts=2,
        confirmation_retry_seconds=1,
        require_csfloat_offer_report=True,
        proxy=None,
        steam_proxy=None,
        user_agent="test",
        data_dir=data_dir,
        config_path=data_dir / "config.json",
    )


class FakeCSFloat:
    def __init__(self, report_error=False):
        self.accepted = False
        self.offer_id = None
        self.report_error = report_error

    async def get_me(self):
        return {"user": {"steam_id": str(SELLER)}}

    async def get_trades(self):
        return [raw_order(accepted=self.accepted, offer_id=self.offer_id)]

    async def get_trade(self, trade_id):
        return raw_order(accepted=self.accepted, offer_id=self.offer_id)

    async def accept_trade(self, trade_id):
        self.accepted = True
        return {"data": [raw_order(accepted=True)]}

    async def report_new_offer(self, offer_id, asset_ids):
        if self.report_error:
            raise CSFloatAPIError("report failed", status=403)
        self.offer_id = offer_id
        return {"message": "successfully updated offer state"}


class FakeSteam:
    def __init__(self):
        self.confirmed = False

    async def find_recovery_offer(self, order):
        return None

    async def create_offer(self, order):
        return 777

    async def get_offer(self, offer_id):
        return object()

    def validate_offer(self, offer, order):
        return None

    async def confirm_offer(self, offer_id, order):
        self.confirmed = True
        return object()


class RateLimitedSteam(FakeSteam):
    async def confirm_offer(self, offer_id, order):
        raise SteamRateLimitError("Steam移动确认接口限流（HTTP 429）")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_rate_limit_keeps_offer_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.db")
            csfloat = FakeCSFloat()
            steam = RateLimitedSteam()
            service = DeliveryService(live_config(Path(temp)), csfloat, state, steam)
            await service.run_once()
            record = state.get("1001")
            self.assertEqual(record.status, "retryable_error")
            self.assertEqual(record.offer_id, 777)
            self.assertEqual(record.attempts, 0)
            self.assertNotIn("https://", record.last_error)
            state.close()

    async def test_completed_pending_trade_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.db")
            csfloat = FakeCSFloat()
            csfloat.accepted = True
            csfloat.offer_id = 888

            async def completed_trades():
                return [raw_order(accepted=True, offer_id=888, offer_state=3)]

            csfloat.get_trades = completed_trades
            steam = FakeSteam()
            service = DeliveryService(live_config(Path(temp)), csfloat, state, steam)
            await service.run_once()
            self.assertFalse(steam.confirmed)
            self.assertEqual(state.get("1001").offer_id, 888)
            state.close()

    async def test_complete_delivery_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.db")
            csfloat = FakeCSFloat()
            steam = FakeSteam()
            service = DeliveryService(live_config(Path(temp)), csfloat, state, steam)
            await service.run_once()
            record = state.get("1001")
            self.assertEqual(record.status, "dispatched")
            self.assertEqual(record.offer_id, 777)
            self.assertTrue(steam.confirmed)
            state.close()

    async def test_required_report_failure_stops_before_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.db")
            csfloat = FakeCSFloat(report_error=True)
            steam = FakeSteam()
            service = DeliveryService(live_config(Path(temp)), csfloat, state, steam)
            await service.run_once()
            record = state.get("1001")
            self.assertEqual(record.status, "retryable_error")
            self.assertFalse(steam.confirmed)
            state.close()


if __name__ == "__main__":
    unittest.main()
