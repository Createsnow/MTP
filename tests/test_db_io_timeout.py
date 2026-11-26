"""连接读写超时（DB_IO_TIMEOUT）：卡住的查询到点断开并归还名额；启动初始化、每日维护（no_io_timeout）里不设超时。"""

from __future__ import annotations

import pymysql
import pytest

from app.db.mysql import Db, is_connection_lost


def test_io_timeout_setting(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("DB_IO_TIMEOUT", raising=False)
    s = Settings()
    cases = {None: 600, "": 600, "abc": 600, "900": 900, "100": 300, "0": None, "-1": None}
    for raw, want in cases.items():
        s._dotenv = {} if raw is None else {"DB_IO_TIMEOUT": raw}
        assert s.db_io_timeout == want, raw


def test_pymysql_reads_timeout_attributes():
    """池里取出的连接靠改 _read_timeout / _write_timeout 切换超时（实际生效见池内复用用例）。
    升级 PyMySQL 改名时这里先失败。"""
    conn = pymysql.connections.Connection(defer_connect=True, read_timeout=5, write_timeout=6)
    assert (conn._read_timeout, conn._write_timeout) == (5, 6)


@pytest.fixture
def short_db(app_client):
    from app.db.session import db as app_db

    d = Db()
    d.configure(app_db.target, 1, io_timeout=1)
    yield d
    d.close()


def test_stuck_query_times_out_and_returns_slot(short_db):
    with pytest.raises(pymysql.err.OperationalError) as e:
        short_db.scalar("SELECT SLEEP(3)")
    assert is_connection_lost(e.value)
    # 断开的连接已丢弃、名额已归还（池大小 1，不会等满 30 秒）
    assert short_db.scalar("SELECT 1") == 1
    with pytest.raises(pymysql.err.OperationalError), short_db.tx():
        short_db.scalar("SELECT SLEEP(3)")
    with short_db.tx():
        assert short_db.scalar("SELECT 2") == 2


def test_no_io_timeout_scope_and_pool_reuse(short_db):
    with short_db.no_io_timeout():
        assert short_db.scalar("SELECT SLEEP(2)") == 0
        # 命名锁连接（池外独立连接）也不设超时
        raw = short_db.connect_raw()
        assert raw._read_timeout is None
        raw.close()
    # 同一条池内连接出了作用域再取：恢复超时
    with pytest.raises(pymysql.err.OperationalError):
        short_db.scalar("SELECT SLEEP(3)")
    raw = short_db.connect_raw()
    assert (raw._read_timeout, raw._write_timeout) == (1, 1)
    raw.close()


def test_app_db_uses_configured_timeout(app_client):
    """应用按 DB_IO_TIMEOUT（默认 600）配置；启动初始化结束后请求里取的连接带超时。"""
    from app.db.session import db as app_db

    with app_db.connection() as conn:
        assert (conn._read_timeout, conn._write_timeout) == (600, 600)
