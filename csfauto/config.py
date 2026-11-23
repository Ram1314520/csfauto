from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


LIVE_PHRASE = "I_UNDERSTAND_THIS_SENDS_REAL_STEAM_OFFERS"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Config:
    mode: Literal["dry_run", "live"]
    live_confirmation_phrase: str
    csfloat_api_key: str
    steam_id64: int
    steam_login: str
    steam_password: str
    steam_api_key: str
    shared_secret: str
    identity_secret: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    max_retries: int
    max_order_attempts: int
    confirmation_attempts: int
    confirmation_retry_seconds: int
    require_csfloat_offer_report: bool
    proxy: str | None
    steam_proxy: str | None
    user_agent: str
    data_dir: Path
    config_path: Path

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def _require_string(raw: dict, key: str, *, allow_empty: bool = False) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ConfigError(f"{key} 必须是字符串")
    value = value.strip()
    if not value and not allow_empty:
        raise ConfigError(f"{key} 不能为空")
    return value


def _positive_int(raw: dict, key: str, default: int, minimum: int = 1) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{key} 必须是大于等于 {minimum} 的整数")
    return value


def _optional_url(raw: dict, key: str, schemes: tuple[str, ...]) -> str | None:
    value = raw.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.startswith(schemes):
        raise ConfigError(f"{key} 的代理协议不受支持")
    return value


def _validate_secret(name: str, value: str) -> None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigError(f"{name} 不是有效的 Base64") from exc
    if len(decoded) < 10:
        raise ConfigError(f"{name} 解码后长度异常")


def load_config(path: str | Path) -> Config:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 JSON 格式错误：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象")

    mode = raw.get("mode", "dry_run")
    if mode not in {"dry_run", "live"}:
        raise ConfigError("mode 只能是 dry_run 或 live")

    api_key = _require_string(raw, "csfloat_api_key")
    steam_id_text = _require_string(raw, "steam_id64")
    if not steam_id_text.isdigit():
        raise ConfigError("steam_id64 必须是数字字符串")
    steam_id = int(steam_id_text)
    if steam_id < 76561197960265728:
        raise ConfigError("steam_id64 数值异常")

    live_phrase = _require_string(raw, "live_confirmation_phrase", allow_empty=True)
    login = _require_string(raw, "steam_login", allow_empty=mode == "dry_run")
    password = _require_string(raw, "steam_password", allow_empty=mode == "dry_run")
    steam_api_key = _require_string(raw, "steam_api_key", allow_empty=mode == "dry_run")
    shared_secret = _require_string(raw, "shared_secret", allow_empty=mode == "dry_run")
    identity_secret = _require_string(raw, "identity_secret", allow_empty=mode == "dry_run")

    if mode == "live":
        if live_phrase != LIVE_PHRASE:
            raise ConfigError(f"live 模式必须将 live_confirmation_phrase 设置为 {LIVE_PHRASE}")
        _validate_secret("shared_secret", shared_secret)
        _validate_secret("identity_secret", identity_secret)

    require_report = raw.get("require_csfloat_offer_report", True)
    if not isinstance(require_report, bool):
        raise ConfigError("require_csfloat_offer_report 必须是布尔值")

    data_value = raw.get("data_dir", "data")
    if not isinstance(data_value, str) or not data_value.strip():
        raise ConfigError("data_dir 必须是非空字符串")
    data_dir = Path(data_value)
    if not data_dir.is_absolute():
        data_dir = config_path.parent / data_dir

    return Config(
        mode=mode,
        live_confirmation_phrase=live_phrase,
        csfloat_api_key=api_key,
        steam_id64=steam_id,
        steam_login=login,
        steam_password=password,
        steam_api_key=steam_api_key,
        shared_secret=shared_secret,
        identity_secret=identity_secret,
        poll_interval_seconds=_positive_int(raw, "poll_interval_seconds", 300, 30),
        request_timeout_seconds=_positive_int(raw, "request_timeout_seconds", 30, 5),
        max_retries=_positive_int(raw, "max_retries", 4, 1),
        max_order_attempts=_positive_int(raw, "max_order_attempts", 5, 1),
        confirmation_attempts=_positive_int(raw, "confirmation_attempts", 8, 1),
        confirmation_retry_seconds=_positive_int(raw, "confirmation_retry_seconds", 3, 1),
        require_csfloat_offer_report=require_report,
        proxy=_optional_url(raw, "proxy", ("http://", "https://")),
        steam_proxy=_optional_url(raw, "steam_proxy", ("http://", "https://", "socks4://", "socks5://")),
        user_agent=_require_string(raw, "user_agent"),
        data_dir=data_dir.resolve(),
        config_path=config_path,
    )

