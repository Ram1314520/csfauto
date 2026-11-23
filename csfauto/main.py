from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime

from .config import ConfigError, load_config
from .csfloat_api import CSFloatClient
from .service import DeliveryService
from .state import StateStore
from .steam_client import SteamDeliveryClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CSFloat automatic Steam delivery service")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮检查")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


async def async_main(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(config.data_dir / "state.db")
    steam: SteamDeliveryClient | None = None
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        if config.is_live:
            steam = SteamDeliveryClient(config)
            await steam.start()
        async with CSFloatClient(
            config.csfloat_api_key,
            timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
            proxy=config.proxy,
        ) as csfloat:
            service = DeliveryService(config, csfloat, state, steam)
            while not stop_event.is_set():
                try:
                    await service.run_once()
                except Exception:
                    logging.getLogger("csfauto").exception("轮询失败")
                    if args.once:
                        raise
                if args.once:
                    break
                completed_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                logging.getLogger("csfauto").info(
                    "当前轮已于 %s 处理完成，等待 %d 秒进行下一轮处理",
                    completed_at,
                    config.poll_interval_seconds,
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=config.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass
    finally:
        if steam is not None:
            await steam.close()
        state.close()


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(async_main(args))
    except ConfigError as exc:
        raise SystemExit(f"配置错误：{exc}") from exc


if __name__ == "__main__":
    main()

