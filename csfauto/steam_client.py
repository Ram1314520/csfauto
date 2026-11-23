from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path

from aiohttp import ClientConnectionError, ClientResponseError
from aiosteampy import AppContext, SteamClient
from aiosteampy.constants import TradeOfferStatus
from aiosteampy.helpers import restore_from_cookies
from aiosteampy.models import EconItem, ItemDescription, TradeOffer
from aiosteampy.utils import get_jsonable_cookies

from .config import Config
from .models import DeliveryOrder, exact_asset_match


class SteamDeliveryError(RuntimeError):
    pass


class SteamRateLimitError(SteamDeliveryError):
    pass


class SteamNetworkError(SteamDeliveryError):
    """A transient Steam transport failure that is safe to retry later."""


MOBILECONF_USER_AGENT = "okhttp/4.9.2"


@contextmanager
def _mobile_confirmation_headers(headers, requested_with: str):
    """Temporarily emulate the official Steam mobile confirmation client."""
    old_user_agent = headers.get("User-Agent")
    old_requested_with = headers.get("X-Requested-With")
    headers["User-Agent"] = MOBILECONF_USER_AGENT
    headers["X-Requested-With"] = requested_with
    try:
        yield
    finally:
        if old_user_agent is None:
            headers.pop("User-Agent", None)
        else:
            headers["User-Agent"] = old_user_agent
        if old_requested_with is None:
            headers.pop("X-Requested-With", None)
        else:
            headers["X-Requested-With"] = old_requested_with


def _sanitize_inventory_description(data: dict) -> dict:
    """Ignore malformed CS2 inspect actions that aiosteampy 0.7.21 cannot parse.

    Steam sometimes returns an action named ``Inspect`` without the ``%D``
    placeholder expected by aiosteampy.  The inspect action is unrelated to
    sending a trade offer, so dropping only that malformed action preserves
    the inventory item while avoiding an IndexError during inventory parsing.
    """
    actions = data.get("actions")
    if not isinstance(actions, list):
        return data
    filtered = [
        action
        for action in actions
        if not (
            isinstance(action, dict)
            and "Inspect" in str(action.get("name", ""))
            and "%D" not in str(action.get("link", ""))
        )
    ]
    if len(filtered) == len(actions):
        return data
    sanitized = dict(data)
    sanitized["actions"] = filtered
    return sanitized


class CompatibleSteamClient(SteamClient):
    @classmethod
    def _create_item_descr(cls, data: dict) -> ItemDescription:
        return super()._create_item_descr(_sanitize_inventory_description(data))

    async def get_confirmations(self, *, update_listings=True):
        with _mobile_confirmation_headers(
            self.session.headers,
            "com.valvesoftware.android.steam.community",
        ):
            return await super().get_confirmations(update_listings=update_listings)

    async def send_confirmation(self, conf, tag):
        with _mobile_confirmation_headers(self.session.headers, "XMLHttpRequest"):
            return await super().send_confirmation(conf, tag)


class SteamDeliveryClient:
    def __init__(self, config: Config):
        self._config = config
        self._cookie_path = config.data_dir / "steam_cookies.json"
        self.client = CompatibleSteamClient(
            steam_id=config.steam_id64,
            username=config.steam_login,
            password=config.steam_password,
            shared_secret=config.shared_secret,
            identity_secret=config.identity_secret,
            api_key=config.steam_api_key or None,
            proxy=config.steam_proxy,
            user_agent=config.user_agent,
        )

    async def start(self) -> None:
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        if self._cookie_path.is_file():
            try:
                cookies = json.loads(self._cookie_path.read_text(encoding="utf-8"))
                await restore_from_cookies(cookies, self.client)
                await self.save_session()
                return
            except Exception:
                # A stale/corrupt cookie cache must never prevent credential login.
                pass
        try:
            await self.client.login()
            session_alive = await self.client.is_session_alive()
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            raise SteamNetworkError(f"Steam登录网络请求失败：{type(exc).__name__}") from None
        if not session_alive:
            raise SteamDeliveryError("Steam登录后会话仍无效")
        await self.save_session()

    async def save_session(self) -> None:
        temp_path = self._cookie_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(get_jsonable_cookies(self.client.session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._cookie_path)

    async def close(self) -> None:
        try:
            await self.save_session()
        finally:
            await self.client.session.close()

    async def inventory_items(self, asset_ids: tuple[int, ...]) -> list[EconItem]:
        wanted = set(asset_ids)
        found: dict[int, EconItem] = {}
        try:
            async for items, _total, _last_asset_id in self.client.inventory(AppContext.CS2, count=2000):
                for item in items:
                    if item.asset_id in wanted:
                        found[item.asset_id] = item
                if wanted == set(found):
                    break
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            raise SteamNetworkError(f"读取Steam库存网络请求失败：{type(exc).__name__}") from None
        if set(found) != wanted:
            missing = sorted(wanted - set(found))
            raise SteamDeliveryError(f"Steam库存缺少目标资产：{missing}")
        return [found[asset_id] for asset_id in asset_ids]

    @staticmethod
    def validate_offer(offer: TradeOffer, order: DeliveryOrder) -> None:
        actual_give = tuple(item.asset_id for item in offer.items_to_give)
        actual_receive = tuple(item.asset_id for item in offer.items_to_receive)
        if not offer.is_our_offer:
            raise SteamDeliveryError("目标报价不是当前账号发出的报价")
        if offer.receiver != order.buyer_id:
            raise SteamDeliveryError("Steam报价接收者与CSFloat买家不一致")
        if not exact_asset_match(actual_give, order.asset_ids):
            raise SteamDeliveryError("Steam报价给出资产与CSFloat订单不完全一致")
        if actual_receive:
            raise SteamDeliveryError("Steam报价包含不应接收的资产")

    async def get_offer(self, offer_id: int, *, attempts: int = 1) -> TradeOffer:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self.client.get_trade_offer(offer_id)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(2)
        if isinstance(last_error, (ClientConnectionError, asyncio.TimeoutError)):
            raise SteamNetworkError(
                f"读取Steam报价网络请求失败 offer_id={offer_id} error={type(last_error).__name__}"
            ) from None
        raise SteamDeliveryError(f"无法读取Steam报价 {offer_id}") from last_error

    async def find_recovery_offer(self, order: DeliveryOrder) -> TradeOffer | None:
        marker = f"[CSFLOAT:{order.trade_id}]"
        matches: list[TradeOffer] = []
        cursor = 0
        while True:
            try:
                sent, _received, cursor = await self.client.get_trade_offers(
                    active_only=True,
                    sent=True,
                    received=False,
                    cursor=cursor,
                )
            except (ClientConnectionError, asyncio.TimeoutError) as exc:
                raise SteamNetworkError(
                    f"查询Steam已发送报价网络请求失败：{type(exc).__name__}"
                ) from None
            for offer in sent:
                if marker not in (offer.message or ""):
                    continue
                try:
                    self.validate_offer(offer, order)
                except SteamDeliveryError:
                    continue
                if offer.status in {TradeOfferStatus.ACTIVE, TradeOfferStatus.CONFIRMATION_NEED}:
                    matches.append(offer)
            if not cursor:
                break
        if len(matches) > 1:
            raise SteamDeliveryError("发现多个完全匹配的活动报价，需要人工检查")
        return matches[0] if matches else None

    async def create_offer(self, order: DeliveryOrder) -> int:
        items = await self.inventory_items(order.asset_ids)
        message = f"[CSFLOAT:{order.trade_id}] CSFloat delivery"
        try:
            offer_id = await self.client.make_trade_offer(
                order.trade_url,
                to_give=items,
                to_receive=[],
                message=message,
                confirm=False,
            )
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            raise SteamNetworkError(f"创建Steam报价网络请求失败：{type(exc).__name__}") from None
        offer = await self.get_offer(int(offer_id), attempts=5)
        self.validate_offer(offer, order)
        if offer.status not in {TradeOfferStatus.ACTIVE, TradeOfferStatus.CONFIRMATION_NEED}:
            raise SteamDeliveryError(f"新Steam报价状态异常：{offer.status.name}")
        return int(offer_id)

    async def confirm_offer(self, offer_id: int, order: DeliveryOrder) -> TradeOffer:
        offer = await self.get_offer(offer_id)
        self.validate_offer(offer, order)
        if offer.status in {TradeOfferStatus.ACCEPTED, TradeOfferStatus.STATE_IN_ESCROW}:
            return offer
        if offer.status is TradeOfferStatus.ACTIVE:
            return offer
        if offer.status is not TradeOfferStatus.CONFIRMATION_NEED:
            raise SteamDeliveryError(f"报价不可确认，当前状态：{offer.status.name}")

        last_error: Exception | None = None
        for _attempt in range(1, self._config.confirmation_attempts + 1):
            try:
                await self.client.confirm_trade_offer(offer_id)
                break
            except ClientResponseError as exc:
                if exc.status == 429:
                    raise SteamRateLimitError(
                        "Steam移动确认接口限流（HTTP 429）；已创建的报价会被保留，请稍后重试"
                    ) from None
                raise SteamDeliveryError(f"Steam移动确认请求失败：HTTP {exc.status}") from None
            except (ClientConnectionError, asyncio.TimeoutError) as exc:
                raise SteamNetworkError(
                    f"Steam移动确认网络请求失败：{type(exc).__name__}"
                ) from None
            except KeyError as exc:
                last_error = exc
                await asyncio.sleep(self._config.confirmation_retry_seconds)
        else:
            raise SteamDeliveryError("重试后仍未找到该报价的移动确认") from last_error

        for _attempt in range(1, self._config.confirmation_attempts + 1):
            await asyncio.sleep(self._config.confirmation_retry_seconds)
            offer = await self.get_offer(offer_id)
            self.validate_offer(offer, order)
            if offer.status is TradeOfferStatus.ACTIVE:
                await self.save_session()
                return offer
            if offer.status is not TradeOfferStatus.CONFIRMATION_NEED:
                raise SteamDeliveryError(f"确认后报价进入异常状态：{offer.status.name}")
        raise SteamDeliveryError("移动确认已提交，但Steam报价状态没有变为ACTIVE")

