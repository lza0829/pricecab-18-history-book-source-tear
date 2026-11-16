from __future__ import annotations

import sqlite3
from typing import Any


def next_batch_no(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM apply_batches").fetchone()["c"] + 1
    return f"AB-{n:04d}"


def create_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    clock_at: str,
    ticket_count: int,
    line_count: int,
    skipped_count: int,
    strategy: str,
    status: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO apply_batches(batch_no, clock_at, ticket_count, line_count,
                                  skipped_count, strategy, status, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        ''',
        (batch_no, clock_at, ticket_count, line_count, skipped_count, strategy, status, created_at),
    )
    return int(cur.lastrowid)


def add_batch_line(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    queue_pos: int,
    ticket_id: int,
    ticket_no: str,
    sku_code: str,
    new_price: float,
    status: str,
    covered_by_ticket_no: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO apply_batch_lines(batch_id, queue_pos, ticket_id, ticket_no, sku_code,
                                      new_price, status, covered_by_ticket_no, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ''',
        (batch_id, queue_pos, ticket_id, ticket_no, sku_code,
         new_price, status, covered_by_ticket_no, created_at),
    )
    return int(cur.lastrowid)


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM apply_batches WHERE id=?", (batch_id,)).fetchone()
    return dict(row) if row else None


def list_batch_lines(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batch_lines WHERE batch_id=? ORDER BY queue_pos, id",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
