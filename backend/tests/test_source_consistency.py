"""本末来源收口测试：全局唯一策略「以价格历史末行（生效时刻+写入序）为准」。

三条互证路径：
1. 展示来源≠历史末行 —— 回填单只进历史参与 as-of，不改价签本现行展示；
2. 对账应生效≠展示价 —— 对账 expected 价/来源与价签本、历史末行同源；
3. 作废/未应用单仍挂来源 —— covered / scheduled 单不得出现在任何来源展示中。
另：种子初始价覆盖当日全部销售夹具（as-of 口径不随墙钟漂移）。
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


def set_clock(client, clock):
    r = client.put("/api/apply/clock", json={"business_clock": clock})
    assert r.status_code == 200, r.text


def book_by_sku(client):
    return {b["sku_code"]: b for b in client.get("/api/prices/book").json()}


def skus_by_code(client):
    return {s["sku_code"]: s for s in client.get("/api/skus").json()}


def reconcile_rows(client):
    return {r["sale_no"]: r for r in client.get("/api/reconcile/sales").json()["rows"]}


def test_seed_prices_cover_all_fixture_sales(client):
    """种子初始价自营业日起点生效：当日销售无缺价，来源为初始（NULL）。"""
    rows = reconcile_rows(client)
    assert all(r["status"] != "missing_price" for r in rows.values())
    assert rows["S-1001"]["expected_price"] == 28.0
    assert rows["S-1001"]["status"] == "match"
    assert rows["S-1001"]["expected_source_ticket_no"] is None
    # S-1003 成交 42.0 vs 初始价 45.0 → 不一致，应生效来源为初始
    assert rows["S-1003"]["expected_price"] == 45.0
    assert rows["S-1003"]["status"] == "mismatch"
    assert rows["S-1003"]["expected_source_ticket_no"] is None


def test_backdated_ticket_does_not_rewrite_current_book(client):
    """回填单（生效时刻早于历史末行）只进历史，价签本/来源展示仍对齐末行。"""
    t1 = make_ticket(client, "正式调价", "2026-11-16T10:00:00Z",
                     [{"sku_code": "SKU-TEA-001", "new_price": 30.0}])
    set_clock(client, "2026-11-16T10:30:00Z")
    assert client.post("/api/apply/due").json()["applied"] is True

    # 回填单：生效时刻 09:30 早于末行 10:00，但应用时刻更晚
    t2 = make_ticket(client, "回填单", "2026-11-16T09:30:00Z",
                     [{"sku_code": "SKU-TEA-001", "new_price": 27.0}])
    assert client.post("/api/apply/due").json()["applied"] is True

    # 价签本 = 历史末行（t1）：展示价/来源/生效时刻不被回填单改写
    book = book_by_sku(client)["SKU-TEA-001"]
    assert book["current_price"] == 30.0
    assert book["source_ticket_no"] == t1["ticket_no"]
    assert book["effective_at"] == "2026-11-16T10:00:00Z"

    # 历史：末行是 t1；回填行仍在链中（as-of 09:30~10:00 → 27.0），不得倒删
    hist = client.get("/api/prices/history", params={"sku_code": "SKU-TEA-001"}).json()
    assert hist[0]["source_ticket_no"] == t1["ticket_no"]
    assert hist[0]["price"] == 30.0
    assert any(h["source_ticket_no"] == t2["ticket_no"] and h["price"] == 27.0 for h in hist)

    # 应用台来源 API 与价签本同源同价
    src = client.get("/api/prices/source/SKU-TEA-001").json()
    assert src["source_ticket_no"] == t1["ticket_no"]
    assert src["current_price"] == 30.0
    assert src["effective_at"] == "2026-11-16T10:00:00Z"
    assert src["source_ticket_status"] == "applied"

    # 商品列表与价签本同源
    sku = skus_by_code(client)["SKU-TEA-001"]
    assert sku["current_price"] == 30.0
    assert sku["source_ticket_no"] == t1["ticket_no"]


def test_reconcile_expected_source_aligned_with_book_and_history(client):
    """对账 expected 价/来源、价签本现行、历史末行、应用台来源四方同源。"""
    t = make_ticket(client, "对齐销售价", "2026-11-16T10:00:00Z",
                    [{"sku_code": "SKU-DAY-021", "new_price": 42.0}])
    set_clock(client, "2026-11-16T10:30:00Z")
    assert client.post("/api/apply/due").json()["applied"] is True

    # S-1003 成交 10:20 @42.0：as-of 命中调价行，来源同单
    rows = reconcile_rows(client)
    s1003 = rows["S-1003"]
    assert s1003["expected_price"] == 42.0
    assert s1003["status"] == "match"
    assert s1003["expected_source_ticket_no"] == t["ticket_no"]
    assert s1003["expected_effective_at"] == "2026-11-16T10:00:00Z"

    # 成交时刻早于调价生效的销售仍对初始行
    s1001 = rows["S-1001"]
    assert s1001["expected_price"] == 28.0
    assert s1001["expected_source_ticket_no"] is None

    # 价签本 / 历史末行 / 应用台来源与对账 expected 来源一致
    book = book_by_sku(client)["SKU-DAY-021"]
    assert book["current_price"] == 42.0
    assert book["source_ticket_no"] == t["ticket_no"]
    hist = client.get("/api/prices/history", params={"sku_code": "SKU-DAY-021"}).json()
    assert hist[0]["source_ticket_no"] == t["ticket_no"]
    src = client.get("/api/prices/source/SKU-DAY-021").json()
    assert src["source_ticket_no"] == t["ticket_no"]
    assert src["source_ticket_status"] == "applied"


def test_covered_and_scheduled_tickets_never_become_source(client):
    """作废（covered）/未应用（scheduled）单不得充当现行来源。"""
    t1 = make_ticket(client, "被覆盖单", "2026-11-16T08:00:00Z",
                     [{"sku_code": "SKU-TEA-002", "new_price": 60.0}])
    t2 = make_ticket(client, "胜出单", "2026-11-16T08:00:00Z",
                     [{"sku_code": "SKU-TEA-002", "new_price": 55.0}])
    client.post("/api/apply/due")
    tickets = {t["ticket_no"]: t for t in client.get("/api/tickets").json()}
    assert tickets[t1["ticket_no"]]["status"] == "covered"

    # 未到期单：scheduled，永不应用
    t3 = make_ticket(client, "未到期单", "2099-01-01T00:00:00Z",
                     [{"sku_code": "SKU-TEA-002", "new_price": 1.0}])

    # 所有来源展示只认胜出且已应用的 t2
    book = book_by_sku(client)["SKU-TEA-002"]
    assert book["current_price"] == 55.0
    assert book["source_ticket_no"] == t2["ticket_no"]
    src = client.get("/api/prices/source/SKU-TEA-002").json()
    assert src["source_ticket_no"] == t2["ticket_no"]
    assert src["source_ticket_status"] == "applied"
    sku = skus_by_code(client)["SKU-TEA-002"]
    assert sku["source_ticket_no"] == t2["ticket_no"]
    hist = client.get("/api/prices/history", params={"sku_code": "SKU-TEA-002"}).json()
    assert all(h["source_ticket_no"] not in (t1["ticket_no"], t3["ticket_no"]) for h in hist)
