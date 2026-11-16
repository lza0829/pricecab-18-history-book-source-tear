"""应用到期批次的可核证性测试。

覆盖任务要求的三条主路径：
1. 无冲突成功 —— 批次行 / 价签本 / 历史末行 / 响应同源；
2. 同商品冲突 —— 全局唯一策略「后写覆盖」：队序最后一单写入，前列记跳过且可核；
3. 应用进行中拨业务钟 / 第二次应用 —— 拒绝且不留半截价签。
另：中途失败整体回滚。
"""
from __future__ import annotations

SEED_CLOCK = "2026-11-16T09:00:00Z"


def make_ticket(client, title, effective_at, lines):
    r = client.post(
        "/api/tickets",
        json={"title": title, "effective_at": effective_at, "lines": lines},
    )
    assert r.status_code == 200, r.text
    return r.json()


def book_by_sku(client):
    return {b["sku_code"]: b for b in client.get("/api/prices/book").json()}


def tickets_by_no(client):
    return {t["ticket_no"]: t for t in client.get("/api/tickets").json()}


def test_apply_due_no_conflict_success(client):
    t1 = make_ticket(client, "茶调价", "2026-11-16T08:00:00Z",
                     [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    t2 = make_ticket(client, "零食调价", "2026-11-16T08:30:00Z",
                     [{"sku_code": "SKU-SNK-011", "new_price": 10.0}])

    r = client.post("/api/apply/due")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["strategy"] == "last_write_wins"
    assert body["ticket_count"] == 2
    assert body["line_count"] == 2
    assert body["skipped_count"] == 0
    assert body["tickets"] == [t1["ticket_no"], t2["ticket_no"]]

    # 价签本现行价与来源单
    book = book_by_sku(client)
    assert book["SKU-TEA-001"]["current_price"] == 25.0
    assert book["SKU-TEA-001"]["source_ticket_no"] == t1["ticket_no"]
    assert book["SKU-SNK-011"]["current_price"] == 10.0
    assert book["SKU-SNK-011"]["source_ticket_no"] == t2["ticket_no"]

    # 价格历史末行与价签本同源
    hist = client.get("/api/prices/history", params={"sku_code": "SKU-TEA-001"}).json()
    assert hist[0]["price"] == 25.0
    assert hist[0]["source_ticket_no"] == t1["ticket_no"]

    # 批次行与响应同源
    batches = client.get("/api/apply/batches").json()
    assert len(batches) == 1
    b = batches[0]
    assert b["batch_no"] == body["batch_no"]
    assert b["ticket_count"] == 2
    assert b["line_count"] == 2
    assert b["skipped_count"] == 0
    assert b["status"] == "done"
    assert b["strategy"] == "last_write_wins"

    # 票据终态
    tickets = tickets_by_no(client)
    assert tickets[t1["ticket_no"]]["status"] == "applied"
    assert tickets[t2["ticket_no"]]["status"] == "applied"


def test_apply_due_same_sku_last_write_wins(client):
    # t1 队序在前：SKU-TEA-001（将被覆盖）+ SKU-DAY-021（正常应用）
    t1 = make_ticket(client, "早场调价", "2026-11-16T08:00:00Z",
                     [{"sku_code": "SKU-TEA-001", "new_price": 25.0},
                      {"sku_code": "SKU-DAY-021", "new_price": 40.0}])
    # t2 队序在后：同商品 SKU-TEA-001，后写覆盖
    t2 = make_ticket(client, "晚场调价", "2026-11-16T08:05:00Z",
                     [{"sku_code": "SKU-TEA-001", "new_price": 22.0}])

    body = client.post("/api/apply/due").json()
    assert body["applied"] is True
    assert body["ticket_count"] == 2
    assert body["line_count"] == 2      # t1 的 SKU-DAY-021 + t2 的 SKU-TEA-001
    assert body["skipped_count"] == 1   # t1 的 SKU-TEA-001 行被覆盖跳过

    # 同商品只保留队序最后一单的新价，来源单可解释
    book = book_by_sku(client)
    assert book["SKU-TEA-001"]["current_price"] == 22.0
    assert book["SKU-TEA-001"]["source_ticket_no"] == t2["ticket_no"]
    assert book["SKU-DAY-021"]["current_price"] == 40.0
    assert book["SKU-DAY-021"]["source_ticket_no"] == t1["ticket_no"]

    # 历史末行 = 队序最后一单；被覆盖单不在该商品历史链中留下价格
    hist = client.get("/api/prices/history", params={"sku_code": "SKU-TEA-001"}).json()
    assert hist[0]["price"] == 22.0
    assert hist[0]["source_ticket_no"] == t2["ticket_no"]
    assert all(h["source_ticket_no"] != t1["ticket_no"] for h in hist)

    # 批明细可核：跳过行、来源单号、覆盖者单号
    detail = client.get(f"/api/apply/batches/{body['batch_id']}").json()
    assert detail["skipped_count"] == 1
    skipped = [l for l in detail["lines"] if l["status"] == "skipped_covered"]
    assert len(skipped) == 1
    assert skipped[0]["ticket_no"] == t1["ticket_no"]
    assert skipped[0]["sku_code"] == "SKU-TEA-001"
    assert skipped[0]["covered_by_ticket_no"] == t2["ticket_no"]
    applied = [l for l in detail["lines"] if l["status"] == "applied"]
    assert len(applied) == 2

    # t1 部分行被覆盖仍记 applied；t2 applied
    tickets = tickets_by_no(client)
    assert tickets[t1["ticket_no"]]["status"] == "applied"
    assert tickets[t2["ticket_no"]]["status"] == "applied"


def test_apply_due_same_effective_at_tiebreak_and_fully_covered(client):
    # 同一生效时刻 → 单号决胜：t2 单号大，队序在后，覆盖 t1
    t1 = make_ticket(client, "礼盒调价A", "2026-11-16T08:00:00Z",
                     [{"sku_code": "SKU-TEA-002", "new_price": 60.0}])
    t2 = make_ticket(client, "礼盒调价B", "2026-11-16T08:00:00Z",
                     [{"sku_code": "SKU-TEA-002", "new_price": 55.0}])

    body = client.post("/api/apply/due").json()
    assert body["applied"] is True
    assert body["line_count"] == 1
    assert body["skipped_count"] == 1
    assert body["covered_tickets"] == [t1["ticket_no"]]

    book = book_by_sku(client)
    assert book["SKU-TEA-002"]["current_price"] == 55.0
    assert book["SKU-TEA-002"]["source_ticket_no"] == t2["ticket_no"]

    # 整单被覆盖 → covered 终态，不再参与后续应用
    tickets = tickets_by_no(client)
    assert tickets[t1["ticket_no"]]["status"] == "covered"
    assert tickets[t2["ticket_no"]]["status"] == "applied"

    # 再次应用：无到期单，被覆盖单不会复活改写价签
    again = client.post("/api/apply/due").json()
    assert again["applied"] is False
    assert book_by_sku(client)["SKU-TEA-002"]["current_price"] == 55.0


def test_clock_change_and_second_apply_rejected_while_apply_in_progress(client):
    from app.services import apply_service

    make_ticket(client, "待应用", "2026-11-16T08:00:00Z",
                [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])

    # 模拟「应用进行中」：持有应用互斥锁
    assert apply_service.APPLY_LOCK.acquire(blocking=False)
    try:
        r = client.put("/api/apply/clock", json={"business_clock": "2026-11-17T00:00:00Z"})
        assert r.status_code == 409
        r2 = client.post("/api/apply/due")
        assert r2.status_code == 409
    finally:
        apply_service.APPLY_LOCK.release()

    # 业务钟未被改写，无半截批次、无价签改动
    assert client.get("/api/apply/clock").json()["business_clock"] == SEED_CLOCK
    assert client.get("/api/apply/batches").json() == []
    assert book_by_sku(client)["SKU-TEA-001"]["current_price"] == 28.0

    # 锁释放后拨钟与应用恢复可用
    assert client.put("/api/apply/clock", json={"business_clock": "2026-11-17T00:00:00Z"}).status_code == 200
    assert client.post("/api/apply/due").json()["applied"] is True


def test_apply_rolls_back_on_error_no_half_written_book(client, monkeypatch):
    make_ticket(client, "第一批", "2026-11-16T08:00:00Z",
                [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    make_ticket(client, "第二批", "2026-11-16T08:01:00Z",
                [{"sku_code": "SKU-SNK-011", "new_price": 9.0}])

    import app.services.apply_service as svc

    real_upsert = svc.price_repo.upsert_book
    calls = {"n": 0}

    def boom(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟写入中途失败")
        return real_upsert(**kw)

    monkeypatch.setattr(svc.price_repo, "upsert_book", boom)

    r = client.post("/api/apply/due")
    assert r.status_code == 500

    # 整体回滚：价签本、历史、批次、票据状态均无半截痕迹
    book = book_by_sku(client)
    assert book["SKU-TEA-001"]["current_price"] == 28.0
    assert book["SKU-TEA-001"]["source_ticket_no"] is None
    assert book["SKU-SNK-011"]["current_price"] == 12.5
    hist = client.get("/api/prices/history", params={"sku_code": "SKU-TEA-001"}).json()
    assert all(h["source_ticket_no"] is None for h in hist)
    assert client.get("/api/apply/batches").json() == []
    assert all(t["status"] == "scheduled" for t in client.get("/api/tickets").json())


def test_apply_due_nothing_due(client):
    body = client.post("/api/apply/due").json()
    assert body["applied"] is False
    assert body["ticket_count"] == 0
    assert body["line_count"] == 0
    assert body["skipped_count"] == 0
    assert client.get("/api/apply/batches").json() == []
