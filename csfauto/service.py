from __future__ import annotations

import asyncio
import logging

from .config import Config
from .csfloat_api import CSFloatAPIError, CSFloatClient
from .models import DeliveryOrder, OrderValidationError
from .state import StateStore
from .steam_client import SteamDeliveryClient, SteamDeliveryError, SteamRateLimitError


logger = logging.getLogger("csfauto")


class DeliveryService:
    def __init__(self, config: Config, csfloat: CSFloatClient, state: StateStore, steam: SteamDeliveryClient | None):
        self.config = config
        self.csfloat = csfloat
        self.state = state
        self.steam = steam

    @staticmethod
    def _csfloat_steam_id(me: dict) -> int | None:
        user = me.get("user") if isinstance(me.get("user"), dict) else me
        value = (user.get("steam_id") or user.get("steam_id64")) if isinstance(user, dict) else None
        try:
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    async def run_once(self) -> None:
        me = await self.csfloat.get_me()
        account_steam_id = self._csfloat_steam_id(me)
        if account_steam_id is not None and account_steam_id != self.config.steam_id64:
            raise RuntimeError("CSFloat API Key绑定的Steam账号与配置steam_id64不一致")

        raw_trades = await self.csfloat.get_trades()
        orders: list[DeliveryOrder] = []
        skipped_completed = 0
        skipped_review = 0
        for raw in raw_trades:
            try:
                order = DeliveryOrder.from_api(raw)
                if order.seller_id != self.config.steam_id64:
                    continue
                order.validate_for_delivery(self.config.steam_id64)
                self.state.observe(order)
                if order.delivery_stage == "completed":
                    skipped_completed += 1
                    logger.info(
                        "跳过已发货订单 trade_id=%s steam_offer_id=%s steam_offer_state=%s item=%s",
                        order.trade_id,
                        order.steam_offer_id,
                        order.steam_offer_state,
                        order.item_name,
                    )
                    continue
                if order.delivery_stage == "manual_review":
                    skipped_review += 1
                    logger.warning(
                        "跳过Steam报价状态异常的订单 trade_id=%s steam_offer_id=%s "
                        "steam_offer_state=%s item=%s",
                        order.trade_id,
                        order.steam_offer_id,
                        order.steam_offer_state,
                        order.item_name,
                    )
                    continue
                orders.append(order)
            except OrderValidationError as exc:
                trade_id = str(raw.get("id", "unknown")) if isinstance(raw, dict) else "unknown"
                logger.warning("跳过无效订单 trade_id=%s reason=%s", trade_id, exc)

        logger.info(
            "订单分类 actionable=%d completed=%d manual_review=%d mode=%s",
            len(orders),
            skipped_completed,
            skipped_review,
            self.config.mode,
        )
        if not self.config.is_live:
            for order in orders:
                logger.info(
                    "DRY-RUN trade_id=%s state=%s action=%s asset_count=%d "
                    "steam_offer_id=%s steam_offer_state=%s item=%s",
                    order.trade_id,
                    order.state,
                    order.delivery_stage,
                    len(order.asset_ids),
                    order.steam_offer_id,
                    order.steam_offer_state,
                    order.item_name,
                )
            return

        if self.steam is None:
            raise RuntimeError("live模式缺少Steam客户端")
        for order in orders:
            try:
                await self._process_order(order)
            except SteamRateLimitError as exc:
                logger.warning("订单等待Steam限流解除 trade_id=%s error=%s", order.trade_id, exc)
                self.state.transition(order.trade_id, "retryable_error", error=str(exc))
            except (SteamDeliveryError, CSFloatAPIError, OrderValidationError) as exc:
                logger.error("订单处理失败 trade_id=%s error=%s", order.trade_id, exc)
                previous = self.state.get(order.trade_id)
                attempts = (previous.attempts if previous else 0) + 1
                status = "manual_review" if attempts >= self.config.max_order_attempts else "retryable_error"
                self.state.transition(
                    order.trade_id,
                    status,
                    error=str(exc)[:500],
                    increment_attempts=True,
                )
            except Exception as exc:
                logger.exception("订单发生未预期异常 trade_id=%s", order.trade_id)
                self.state.transition(
                    order.trade_id,
                    "manual_review",
                    error=f"{type(exc).__name__}: {exc}"[:500],
                    increment_attempts=True,
                )

    async def _refresh_order(self, trade_id: str) -> DeliveryOrder:
        order = DeliveryOrder.from_api(await self.csfloat.get_trade(trade_id))
        order.validate_for_delivery(self.config.steam_id64)
        self.state.observe(order)
        return order

    async def _wait_until_accepted(self, trade_id: str) -> DeliveryOrder:
        for _ in range(10):
            order = await self._refresh_order(trade_id)
            if order.accepted_at and order.state == "pending":
                return order
            await asyncio.sleep(2)
        raise CSFloatAPIError("接受订单后状态未在规定时间内变为pending")

    async def _process_order(self, order: DeliveryOrder) -> None:
        assert self.steam is not None
        record = self.state.get(order.trade_id)
        if record and record.status in {"dispatched", "verified", "manual_review"}:
            if record.status == "manual_review":
                logger.error("订单已停止自动重试，需要人工检查 trade_id=%s", order.trade_id)
            return

        if not order.accepted_at:
            logger.info("接受CSFloat订单 trade_id=%s", order.trade_id)
            await self.csfloat.accept_trade(order.trade_id)
            self.state.transition(order.trade_id, "accepted")
            order = await self._wait_until_accepted(order.trade_id)
        else:
            order = await self._refresh_order(order.trade_id)

        offer_id = order.steam_offer_id or (record.offer_id if record else None)
        if offer_id is None:
            recovered = await self.steam.find_recovery_offer(order)
            if recovered is not None:
                offer_id = recovered.trade_offer_id
                logger.warning("恢复到已存在Steam报价 trade_id=%s offer_id=%s", order.trade_id, offer_id)

        if offer_id is None:
            logger.info("创建Steam报价 trade_id=%s asset_count=%d", order.trade_id, len(order.asset_ids))
            offer_id = await self.steam.create_offer(order)
            self.state.transition(order.trade_id, "offer_created", offer_id=offer_id)

        offer = await self.steam.get_offer(offer_id)
        self.steam.validate_offer(offer, order)

        reported = order.steam_offer_id == offer_id
        if not reported:
            try:
                await self.csfloat.report_new_offer(offer_id, order.asset_ids)
                reported = True
                self.state.transition(order.trade_id, "offer_reported", offer_id=offer_id)
            except CSFloatAPIError:
                if self.config.require_csfloat_offer_report:
                    raise
                logger.warning("CSFloat报价上报失败但配置允许继续 trade_id=%s offer_id=%s", order.trade_id, offer_id)

        logger.info("确认Steam报价 trade_id=%s offer_id=%s", order.trade_id, offer_id)
        await self.steam.confirm_offer(offer_id, order)
        self.state.transition(order.trade_id, "confirmed", offer_id=offer_id)

        for _ in range(10):
            refreshed = await self._refresh_order(order.trade_id)
            if refreshed.steam_offer_id == offer_id:
                self.state.transition(order.trade_id, "dispatched", offer_id=offer_id)
                logger.info("发货流程完成 trade_id=%s offer_id=%s", order.trade_id, offer_id)
                return
            await asyncio.sleep(3)
        if reported:
            raise CSFloatAPIError("Steam报价已确认，但CSFloat尚未显示对应offer_id")

