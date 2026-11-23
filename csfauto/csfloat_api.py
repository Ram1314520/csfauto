from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class CSFloatAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class CSFloatClient:
    BASE_URL = "https://csfloat.com/api/v1"

    def __init__(self, api_key: str, *, timeout_seconds: int, max_retries: int, proxy: str | None = None):
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_retries = max_retries
        self._proxy = proxy
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "CSFloatClient":
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            headers={"Authorization": self._api_key, "Accept": "application/json"},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session:
            await self._session.close()

    async def _request(self, method: str, path: str, *, json_body: dict | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("CSFloatClient尚未启动")
        url = f"{self.BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._session.request(method, url, json=json_body, proxy=self._proxy) as response:
                    if response.status == 429 or response.status >= 500:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 30)
                        if attempt < self._max_retries:
                            await asyncio.sleep(delay)
                            continue
                    if response.status < 200 or response.status >= 300:
                        detail = (await response.text()).replace("\r", " ").replace("\n", " ")[:300]
                        raise CSFloatAPIError(
                            f"CSFloat {method} {path} 失败：HTTP {response.status} {detail}",
                            status=response.status,
                        )
                    if response.status == 204:
                        return None
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(min(2 ** attempt, 30))
                    continue
        raise CSFloatAPIError(f"CSFloat {method} {path} 网络请求失败：{type(last_error).__name__}") from last_error

    async def get_me(self) -> dict:
        data = await self._request("GET", "/me")
        if not isinstance(data, dict):
            raise CSFloatAPIError("CSFloat /me 返回格式异常")
        return data

    async def get_trades(self) -> list[dict]:
        data = await self._request("GET", "/me/trades?state=queued,pending&limit=500")
        if not isinstance(data, dict) or not isinstance(data.get("trades"), list):
            raise CSFloatAPIError("CSFloat交易列表格式异常")
        return data["trades"]

    async def get_trade(self, trade_id: str) -> dict:
        data = await self._request("GET", f"/trades/{trade_id}")
        if not isinstance(data, dict):
            raise CSFloatAPIError("CSFloat交易详情格式异常")
        return data

    async def accept_trade(self, trade_id: str) -> dict:
        data = await self._request("POST", "/trades/bulk/accept", json_body={"trade_ids": [trade_id]})
        if not isinstance(data, dict):
            raise CSFloatAPIError("CSFloat接受订单响应格式异常")
        return data

    async def report_new_offer(self, offer_id: int, asset_ids: tuple[int, ...]) -> dict:
        data = await self._request(
            "POST",
            "/trades/steam-status/new-offer",
            json_body={
                "offer_id": str(offer_id),
                "given_asset_ids": [str(asset_id) for asset_id in asset_ids],
                "received_asset_ids": [],
            },
        )
        if not isinstance(data, dict):
            raise CSFloatAPIError("CSFloat报价上报响应格式异常")
        return data

