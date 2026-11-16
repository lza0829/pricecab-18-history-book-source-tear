from __future__ import annotations

import sqlite3
from typing import Any

# 本末来源收口（全局唯一，不可切换）：以价格历史末行为准。
# - price_history 是唯一事实源，只增不改；末行口径 = 生效时刻 DESC、写入序 id DESC。
# - price_book 的 current_price / source_ticket_no / effective_at 是历史末行的物化投影，
#   写入时整体重算，不存在第二份展示数据（原 price_book_display 影子表已作废）。
# - 对账 as-of 与应用台来源展示读同一张 price_history，不得用展示倒删历史行。


def latest_history_row(conn: sqlite3.Connection, sku_code: str) -> dict[str, Any] | None:
    """历史末行（生效时刻 + 写入序）：价签本投影与来源展示的唯一口径。"""
    row = conn.execute(
        '''
        SELECT id, price, source_ticket_no, effective_at
        FROM price_history
        WHERE sku_code=?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code,),
    ).fetchone()
    return dict(row) if row else None


def list_book(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT b.sku_code, b.current_price, b.source_ticket_no, b.effective_at, b.updated_at,
               s.title, s.category
        FROM price_book b
        JOIN skus s ON s.sku_code = b.sku_code
        ORDER BY b.sku_code
        '''
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_book(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str,
    effective_at: str,
    updated_at: str,
) -> None:
    # 1) 历史先落行（唯一事实源，只增不改）
    conn.execute(
        '''
        INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at)
        VALUES (?,?,?,?,?)
        ''',
        (sku_code, price, ticket_no, effective_at, updated_at),
    )
    # 2) 价签本 = 历史末行投影：四个展示字段整体重算。
    #    回填单（生效时刻早于既有末行）只进历史参与 as-of，不改现行展示。
    last = latest_history_row(conn, sku_code)
    conn.execute(
        '''
        INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(sku_code) DO UPDATE SET
          current_price=excluded.current_price,
          source_ticket_no=excluded.source_ticket_no,
          effective_at=excluded.effective_at,
          updated_at=excluded.updated_at
        ''',
        (sku_code, last["price"], last["source_ticket_no"], last["effective_at"], updated_at),
    )


def list_history(conn: sqlite3.Connection, sku_code: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    # 首行即末行（生效时刻 + 写入序），与价签本 / 对账口径一致
    order = "ORDER BY effective_at DESC, id DESC"
    if sku_code:
        rows = conn.execute(
            f"SELECT * FROM price_history WHERE sku_code=? {order} LIMIT ?",
            (sku_code, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM price_history {order} LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def history_row_at(conn: sqlite3.Connection, sku_code: str, at: str) -> dict[str, Any] | None:
    """as-of 行：成交时刻对应的应生效价与其来源单，同一行同源带出。"""
    row = conn.execute(
        '''
        SELECT price, source_ticket_no, effective_at
        FROM price_history
        WHERE sku_code=? AND effective_at <= ?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code, at),
    ).fetchone()
    return dict(row) if row else None
