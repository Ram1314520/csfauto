from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import DeliveryOrder


@dataclass(frozen=True, slots=True)
class OrderState:
    trade_id: str
    status: str
    offer_id: int | None
    asset_ids: tuple[int, ...]
    buyer_id: int
    last_error: str | None
    attempts: int


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                trade_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                offer_id INTEGER,
                seller_id INTEGER NOT NULL,
                buyer_id INTEGER NOT NULL,
                asset_ids TEXT NOT NULL,
                item_name TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def observe(self, order: DeliveryOrder) -> None:
        now = self._now()
        self._conn.execute(
            """
            INSERT INTO orders (
                trade_id, status, offer_id, seller_id, buyer_id, asset_ids,
                item_name, attempts, last_error, created_at, updated_at
            ) VALUES (?, 'discovered', ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                offer_id=COALESCE(orders.offer_id, excluded.offer_id),
                seller_id=excluded.seller_id,
                buyer_id=excluded.buyer_id,
                asset_ids=excluded.asset_ids,
                item_name=excluded.item_name,
                updated_at=excluded.updated_at
            """,
            (
                order.trade_id,
                order.steam_offer_id,
                order.seller_id,
                order.buyer_id,
                json.dumps(order.asset_ids),
                order.item_name,
                now,
                now,
            ),
        )
        self._conn.commit()

    def get(self, trade_id: str) -> OrderState | None:
        row = self._conn.execute("SELECT * FROM orders WHERE trade_id=?", (trade_id,)).fetchone()
        if row is None:
            return None
        return OrderState(
            trade_id=row["trade_id"],
            status=row["status"],
            offer_id=row["offer_id"],
            asset_ids=tuple(json.loads(row["asset_ids"])),
            buyer_id=row["buyer_id"],
            last_error=row["last_error"],
            attempts=row["attempts"],
        )

    def release_session_expired_reviews(self) -> int:
        """Unlock only legacy manual reviews caused by a transient Steam session expiry."""
        cursor = self._conn.execute(
            """
            UPDATE orders
            SET status='retryable_error', attempts=0,
                last_error='Steam会话过期记录已自动解除，等待重试', updated_at=?
            WHERE status='manual_review' AND last_error LIKE 'SessionExpired:%'
            """,
            (self._now(),),
        )
        self._conn.commit()
        return cursor.rowcount

    def transition(
        self,
        trade_id: str,
        status: str,
        *,
        offer_id: int | None = None,
        error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        updates = ["status=?", "updated_at=?", "last_error=?"]
        values: list[object] = [status, self._now(), error]
        if offer_id is not None:
            updates.append("offer_id=?")
            values.append(offer_id)
        if increment_attempts:
            updates.append("attempts=attempts+1")
        values.append(trade_id)
        self._conn.execute(f"UPDATE orders SET {', '.join(updates)} WHERE trade_id=?", values)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

