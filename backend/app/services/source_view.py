from __future__ import annotations

from typing import Any

from app.db import connect


def ticket_source_for_sku(sku_code: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            '''
            SELECT COALESCE(d.source_ticket_no, b.source_ticket_no) AS source_ticket_no,
                   COALESCE(d.effective_at, b.effective_at) AS effective_at,
                   b.current_price
            FROM price_book b
            LEFT JOIN price_book_display d ON d.sku_code = b.sku_code
            WHERE b.sku_code=?
            ''',
            (sku_code,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
