from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 须在导入任何 app 模块之前设定数据目录（config 在 import 时读取）。
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pricecab-test-session-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试一个全新 SQLite 库（种子数据 + 默认业务钟 2026-11-16T09:00:00Z）。"""
    import app.db as db
    from app.seed import seed_if_empty

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    seed_if_empty()

    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
