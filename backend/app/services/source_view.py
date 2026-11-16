from __future__ import annotations

from typing import Any

from app.db import connect


def ticket_source_for_sku(sku_code: str) -> dict[str, Any] | None:
    """应用台「来源单」：与价签本同口径，直接读价格历史末行（生效时刻 + 写入序）。

    只有实际写入历史的已应用行才能成为现行来源；作废（covered）/未应用
    （scheduled）单不产生历史行，结构上不可能挂在来源上。带出票据状态供核验。
    """
    conn = connect()
    try:
        row = conn.execute(
            '''
            SELECT h.source_ticket_no, h.effective_at, h.price AS current_price,
                   t.status AS source_ticket_status
            FROM price_history h
            LEFT JOIN price_tickets t ON t.ticket_no = h.source_ticket_no
            WHERE h.sku_code=?
            ORDER BY h.effective_at DESC, h.id DESC
            LIMIT 1
            ''',
            (sku_code,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
