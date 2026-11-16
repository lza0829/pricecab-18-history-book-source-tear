from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import connect


FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 种子价格的生效时刻固定为营业日起点：历史 as-of 口径不随启动墙钟漂移，
# 当日全部销售夹具（08:10 起）都能对到初始价。
SEED_EFFECTIVE_AT = "2026-11-16T00:00:00Z"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def init_schema(conn) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skus (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          list_price REAL NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS price_tickets (
          id INTEGER PRIMARY KEY,
          ticket_no TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          effective_at TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          applied_at TEXT,
          apply_batch_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS price_ticket_lines (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER NOT NULL REFERENCES price_tickets(id) ON DELETE CASCADE,
          sku_code TEXT NOT NULL,
          old_price REAL NOT NULL,
          new_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS apply_batches (
          id INTEGER PRIMARY KEY,
          batch_no TEXT NOT NULL UNIQUE,
          clock_at TEXT NOT NULL,
          ticket_count INTEGER NOT NULL,
          line_count INTEGER NOT NULL,
          skipped_count INTEGER NOT NULL DEFAULT 0,
          strategy TEXT NOT NULL DEFAULT 'last_write_wins',
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS apply_batch_lines (
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL REFERENCES apply_batches(id) ON DELETE CASCADE,
          queue_pos INTEGER NOT NULL,
          ticket_id INTEGER NOT NULL,
          ticket_no TEXT NOT NULL,
          sku_code TEXT NOT NULL,
          new_price REAL NOT NULL,
          status TEXT NOT NULL,
          covered_by_ticket_no TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_book (
          sku_code TEXT PRIMARY KEY,
          current_price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL,
          price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales_fixture (
          id INTEGER PRIMARY KEY,
          sale_no TEXT NOT NULL UNIQUE,
          sku_code TEXT NOT NULL,
          sold_at TEXT NOT NULL,
          unit_price REAL NOT NULL,
          qty INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hold_applies (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        '''
    )


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Idempotently add a column to an existing table (for pre-existing DBs)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def repair_book_from_history(conn) -> None:
    """本末来源收口（对存量库幂等自愈）：

    1. 作废分叉的影子展示表 price_book_display（不再有任何读者）；
    2. 价签本展示字段按历史末行（生效时刻 + 写入序）整体重算。
    只重写价签本投影，绝不删改 price_history 事实行。
    """
    conn.execute("DROP TABLE IF EXISTS price_book_display")
    conn.execute(
        '''
        UPDATE price_book
        SET current_price = (SELECT h.price FROM price_history h
                             WHERE h.sku_code=price_book.sku_code
                             ORDER BY h.effective_at DESC, h.id DESC LIMIT 1),
            source_ticket_no = (SELECT h.source_ticket_no FROM price_history h
                                WHERE h.sku_code=price_book.sku_code
                                ORDER BY h.effective_at DESC, h.id DESC LIMIT 1),
            effective_at = (SELECT h.effective_at FROM price_history h
                            WHERE h.sku_code=price_book.sku_code
                            ORDER BY h.effective_at DESC, h.id DESC LIMIT 1)
        WHERE EXISTS (SELECT 1 FROM price_history h WHERE h.sku_code=price_book.sku_code)
        '''
    )


def seed_if_empty() -> None:
    conn = connect()
    try:
        init_schema(conn)
        _ensure_column(conn, "apply_batches", "skipped_count",
                       "skipped_count INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "apply_batches", "strategy",
                       "strategy TEXT NOT NULL DEFAULT 'last_write_wins'")
        repair_book_from_history(conn)
        n = conn.execute("SELECT COUNT(*) c FROM skus").fetchone()["c"]
        if n:
            conn.commit()
            return

        skus = json.loads((FIXTURES / "skus.json").read_text(encoding="utf-8"))
        sales = json.loads((FIXTURES / "sales.json").read_text(encoding="utf-8"))
        now = _now()
        for s in skus:
            conn.execute(
                "INSERT INTO skus(sku_code, title, category, list_price, enabled) VALUES (?,?,?,?,1)",
                (s["sku_code"], s["title"], s["category"], float(s["list_price"])),
            )
            conn.execute(
                "INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), SEED_EFFECTIVE_AT, now),
            )
            conn.execute(
                "INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), SEED_EFFECTIVE_AT, now),
            )
        for row in sales:
            conn.execute(
                "INSERT INTO sales_fixture(sale_no, sku_code, sold_at, unit_price, qty) VALUES (?,?,?,?,?)",
                (row["sale_no"], row["sku_code"], row["sold_at"], float(row["unit_price"]), int(row["qty"])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('business_clock', ?)",
            ("2026-11-16T09:00:00Z",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_label', ?)",
            ("业务钟（可拨，用于判断改价是否到期）",),
        )
        conn.commit()
    finally:
        conn.close()
