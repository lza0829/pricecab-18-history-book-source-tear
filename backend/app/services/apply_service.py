from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import tickets as ticket_repo
from app.repositories import prices as price_repo
from app.repositories import batches as batch_repo

# 同商品冲突策略（全局唯一，刻意不做成可切换开关）：
# 后写覆盖 —— 队序（先生效时刻、再单号）最后一张到期单的新价写入价签本与历史；
# 前列同商品行记为 skipped_covered（被覆盖跳过），批次仍然成功，
# 跳过数与来源单号落在 apply_batch_lines 中可核。
CONFLICT_STRATEGY = "last_write_wins"

# 「应用到期」与「拨业务钟 / 第二次应用到期」互斥：非阻塞取锁，取不到即拒绝（409），
# 不排队、不写半截价签。
APPLY_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_clock() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "business_clock": settings_repo.get(conn, "business_clock"),
            "clock_label": settings_repo.get(conn, "clock_label"),
        }
    finally:
        conn.close()


def set_clock(clock_at: str) -> dict[str, Any]:
    clock_at = (clock_at or "").strip()
    if not clock_at:
        raise HTTPException(400, "业务钟不能为空")
    if not APPLY_LOCK.acquire(blocking=False):
        raise HTTPException(409, "应用到期进行中，禁止拨动业务钟")
    try:
        conn = connect(immediate=True)
        try:
            settings_repo.set_value(conn, "business_clock", clock_at)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    finally:
        APPLY_LOCK.release()
    return get_clock()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def get_batch(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        batch = batch_repo.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(404, "批次不存在")
        batch["lines"] = batch_repo.list_batch_lines(conn, batch_id)
        return batch
    finally:
        conn.close()


def _build_queue(conn, due: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten due tickets (already in queue order) into per-line queue entries."""
    queue: list[dict[str, Any]] = []
    for pos, t in enumerate(due, start=1):
        for line in ticket_repo.list_lines(conn, t["id"]):
            queue.append(
                {
                    "queue_pos": pos,
                    "ticket_id": t["id"],
                    "ticket_no": t["ticket_no"],
                    "effective_at": t["effective_at"],
                    "sku_code": line["sku_code"],
                    "new_price": float(line["new_price"]),
                }
            )
    return queue


def _mark_overwrites(queue: list[dict[str, Any]]) -> None:
    """后写覆盖：同一商品只保留队序最后一行，前列同商品行记为被覆盖跳过。"""
    last_idx_by_sku: dict[str, int] = {}
    for idx, item in enumerate(queue):
        last_idx_by_sku[item["sku_code"]] = idx
    for idx, item in enumerate(queue):
        last = queue[last_idx_by_sku[item["sku_code"]]]
        if last is item:
            item["status"] = "applied"
            item["covered_by_ticket_no"] = None
        else:
            item["status"] = "skipped_covered"
            item["covered_by_ticket_no"] = last["ticket_no"]


def apply_due() -> dict[str, Any]:
    """Apply all scheduled tickets whose effective_at <= business clock.

    单事务、单队列快照：批次行、批明细、价签本、价格历史、票据状态同源写出；
    任何异常整体回滚，不留半截价签。
    """
    if not APPLY_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有应用到期批次进行中，本次应用被拒绝")
    try:
        conn = connect(immediate=True)
        try:
            clock = settings_repo.get(conn, "business_clock")
            if not clock:
                raise HTTPException(400, "业务钟未设置")
            due = ticket_repo.due_tickets(conn, clock)
            if not due:
                conn.commit()
                return {
                    "applied": False,
                    "message": "当前业务钟下没有到期改价单",
                    "business_clock": clock,
                    "strategy": CONFLICT_STRATEGY,
                    "ticket_count": 0,
                    "line_count": 0,
                    "skipped_count": 0,
                }

            queue = _build_queue(conn, due)
            _mark_overwrites(queue)
            applied_lines = [i for i in queue if i["status"] == "applied"]
            skipped_lines = [i for i in queue if i["status"] == "skipped_covered"]
            applied_ticket_ids = {i["ticket_id"] for i in applied_lines}

            created = _now()
            batch_no = batch_repo.next_batch_no(conn)
            batch_id = batch_repo.create_batch(
                conn,
                batch_no=batch_no,
                clock_at=clock,
                ticket_count=len(due),
                line_count=len(applied_lines),
                skipped_count=len(skipped_lines),
                strategy=CONFLICT_STRATEGY,
                status="done",
                created_at=created,
            )
            for item in queue:
                if item["status"] == "applied":
                    price_repo.upsert_book(
                        conn,
                        sku_code=item["sku_code"],
                        price=item["new_price"],
                        ticket_no=item["ticket_no"],
                        effective_at=item["effective_at"],
                        updated_at=created,
                    )
                batch_repo.add_batch_line(
                    conn,
                    batch_id=batch_id,
                    queue_pos=item["queue_pos"],
                    ticket_id=item["ticket_id"],
                    ticket_no=item["ticket_no"],
                    sku_code=item["sku_code"],
                    new_price=item["new_price"],
                    status=item["status"],
                    covered_by_ticket_no=item["covered_by_ticket_no"],
                    created_at=created,
                )
            # 票据状态：全部行被覆盖 → covered（终态，不再参与应用）；
            # 否则 applied（部分行被覆盖的明细见 apply_batch_lines）。
            for t in due:
                status = "applied" if t["id"] in applied_ticket_ids else "covered"
                ticket_repo.mark_status(
                    conn, t["id"], status=status, applied_at=created, batch_id=batch_id
                )

            conn.commit()
            return {
                "applied": True,
                "batch_id": batch_id,
                "batch_no": batch_no,
                "strategy": CONFLICT_STRATEGY,
                "business_clock": clock,
                "ticket_count": len(due),
                "line_count": len(applied_lines),
                "skipped_count": len(skipped_lines),
                "tickets": [t["ticket_no"] for t in due if t["id"] in applied_ticket_ids],
                "covered_tickets": [t["ticket_no"] for t in due if t["id"] not in applied_ticket_ids],
                "lines": [
                    {
                        "ticket_no": i["ticket_no"],
                        "sku_code": i["sku_code"],
                        "new_price": i["new_price"],
                        "status": i["status"],
                        "covered_by_ticket_no": i["covered_by_ticket_no"],
                    }
                    for i in queue
                ],
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    finally:
        APPLY_LOCK.release()
