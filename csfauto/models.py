from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


STEAM_ID64_BASE = 76561197960265728
STEAM_OFFER_ACTIVE = 2
STEAM_OFFER_ACCEPTED = 3
STEAM_OFFER_CONFIRMATION_NEEDED = 9
STEAM_OFFER_IN_ESCROW = 11


class OrderValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryOrder:
    trade_id: str
    state: str
    seller_id: int
    buyer_id: int
    asset_ids: tuple[int, ...]
    accepted_at: str | None
    trade_url: str
    trade_token: str
    wait_for_cancel_ping: bool
    steam_offer_id: int | None
    steam_offer_state: int | None
    item_name: str

    @property
    def delivery_stage(self) -> str:
        """Classify whether CSFloat still needs a seller-side action.

        CSFloat keeps already delivered trades in the ``pending`` state while
        it verifies/settles them.  The attached Steam offer is therefore the
        authoritative discriminator; treating every pending trade as a new
        delivery can otherwise re-process completed sales.
        """
        if self.steam_offer_id is None:
            return "new"
        if self.steam_offer_state in {STEAM_OFFER_ACCEPTED, STEAM_OFFER_IN_ESCROW}:
            return "completed"
        if self.steam_offer_state in {
            None,
            STEAM_OFFER_ACTIVE,
            STEAM_OFFER_CONFIRMATION_NEEDED,
        }:
            return "resume"
        return "manual_review"

    @classmethod
    def from_api(cls, raw: dict) -> "DeliveryOrder":
        try:
            contract = raw["contract"]
            item = contract["item"]
            steam_offer = raw.get("steam_offer") or {}
            asset_id = int(item["asset_id"])
            return cls(
                trade_id=str(raw["id"]),
                state=str(raw["state"]),
                seller_id=int(raw["seller_id"]),
                buyer_id=int(raw["buyer_id"]),
                asset_ids=(asset_id,),
                accepted_at=raw.get("accepted_at"),
                trade_url=str(raw.get("trade_url") or ""),
                trade_token=str(raw.get("trade_token") or ""),
                wait_for_cancel_ping=bool(raw.get("wait_for_cancel_ping", False)),
                steam_offer_id=int(steam_offer["id"]) if steam_offer.get("id") else None,
                steam_offer_state=int(steam_offer["state"]) if steam_offer.get("state") is not None else None,
                item_name=str(item.get("market_hash_name") or item.get("item_name") or "unknown"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderValidationError("CSFloat订单缺少必要字段") from exc

    def validate_for_delivery(self, expected_seller_id: int) -> None:
        if self.seller_id != expected_seller_id:
            raise OrderValidationError("订单卖家不是当前Steam账号")
        if self.buyer_id < STEAM_ID64_BASE:
            raise OrderValidationError("买家SteamID64异常")
        if not self.asset_ids or any(asset <= 0 for asset in self.asset_ids):
            raise OrderValidationError("订单资产ID异常")
        if self.wait_for_cancel_ping:
            raise OrderValidationError("订单正在等待取消确认")
        if self.state not in {"queued", "pending"}:
            raise OrderValidationError(f"订单状态不允许发货：{self.state}")
        validate_trade_url(self.trade_url, self.buyer_id, self.trade_token)


def validate_trade_url(trade_url: str, buyer_steam_id64: int, trade_token: str) -> None:
    parsed = urlparse(trade_url)
    if parsed.scheme != "https" or parsed.hostname not in {"steamcommunity.com", "www.steamcommunity.com"}:
        raise OrderValidationError("交易链接不是Steam HTTPS地址")
    if not parsed.path.rstrip("/").endswith("/tradeoffer/new"):
        raise OrderValidationError("Steam交易链接路径异常")
    query = parse_qs(parsed.query)
    partner_values = query.get("partner", [])
    token_values = query.get("token", [])
    expected_account_id = buyer_steam_id64 - STEAM_ID64_BASE
    try:
        partner_id = int(partner_values[0])
    except (IndexError, TypeError, ValueError) as exc:
        raise OrderValidationError("交易链接缺少有效partner") from exc
    if partner_id != expected_account_id:
        raise OrderValidationError("交易链接partner与买家SteamID不一致")
    if trade_token and (not token_values or token_values[0] != trade_token):
        raise OrderValidationError("交易链接token与订单token不一致")


def exact_asset_match(actual: list[int] | tuple[int, ...], expected: list[int] | tuple[int, ...]) -> bool:
    return len(actual) == len(expected) and sorted(actual) == sorted(expected)

