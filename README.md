# 06-pricecab（价签柜）

Pricecab — 改价单按预约时刻生效，现行价签可查，并与销售夹具对账。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）、Proofpack（采样放行）主链路不同。  
不是电商订单/购物车，不是仓库进出库 CRUD，不是考勤 OA。夹具全本地。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5600 |
| API | http://localhost:10600 |

## 主链（0-1）

选商品建改价单（写明生效时刻）→ 拨业务钟或点「应用到期」→ 现行价签更新 → 销售对账看一致/不一致

## 应用到期的批次语义

- **队序**：到期单先按生效时刻、再按单号排序，逐票逐行展开成应用队列。
- **同商品冲突策略（全局唯一，不可切换）**：后写覆盖（`last_write_wins`）——同一商品只保留队序最后一张单的新价写入价签本与价格历史；前列同商品行记为 `skipped_covered`（被覆盖跳过），批次仍成功。整单被覆盖的改价单记终态 `covered`，不再参与后续应用。
- **可核证**：批次行（`apply_batches`）带 `ticket_count / line_count / skipped_count / strategy`；每行明细落 `apply_batch_lines`（来源单号、行状态、覆盖者单号），经 `GET /api/apply/batches/{id}` 可查；响应、批次行、价签本现行价与来源单、价格历史末行同源于同一份队列计划，单事务写出。
- **互斥**：应用进行中，第二次「应用到期」与「拨业务钟」均被拒绝（409）；批次异常整体回滚，不留半截价签。

## 本末同源（全局唯一收口）

价格历史（`price_history`）是唯一事实源，只增不改；**末行口径 = 生效时刻 + 写入序**。

- 价签本的现行价 / 来源单 / 生效时刻是历史末行的物化投影：应用时整体重算，服务启动时对存量库幂等修复；不存在第二份展示数据，不得用展示倒删历史行。
- 回填单（生效时刻早于末行）只进历史参与 as-of，不改现行展示。
- 对账「应生效价 / 应生效来源」与价签本、应用台来源展示（`GET /api/prices/source/{sku}`）同读历史，三方互证一致。
- 只有实际写入历史的已应用行才能成为现行来源：作废（covered）/未应用（scheduled）单不产生历史行，结构上不可能挂在来源上。

## 测试

```bash
cd backend && pip install -r requirements.txt
python -m pytest tests/
```

覆盖：无冲突成功、同商品冲突（后写覆盖 + 同刻按单号决胜 + 整单被覆盖）、应用中改钟/二次应用被拒、中途失败整体回滚。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
