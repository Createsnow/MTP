"""命名锁按库区分（2026-11-24）：MySQL 的命名锁整台服务器共用、不分库，同一台 MySQL 上的开发 / 测试 / 正式库
锁名相同时会互相挡住（一个环境在同步，别的环境页面显示「同步进行中」、同步被跳过；同一 PI 号互相报 PI 忙；
分区维护被跳过）。锁名带上库名后，别的库占着同名的锁不影响本库，本库的进程之间照常互斥。"""

from __future__ import annotations

from conftest import DB, m, ok, raw_conn, uid

from app.db.migrate import migrate_lock_name
from app.modules.order.services import orders
from app.platform import locks


def _hold(name: str):
    """在另一条连接上占住一个命名锁（模拟别的进程 / 别的环境）。"""
    c = raw_conn(None)
    with c.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0) AS ok", (name,))
        assert cur.fetchone()["ok"] == 1
    return c


def test_lock_names_carry_database():
    a = locks.lock_name("sync", "orders", "sn_dev")
    b = locks.lock_name("sync", "orders", "sn_test")
    assert a != b and a.startswith("sn_sync_") and len(a) <= 64
    assert locks.lock_name("sync", "orders", "sn_dev") == a, "同一库同一键锁名固定"
    assert locks.lock_name("pi", "P1", "sn_dev") != locks.lock_name("pi", "P2", "sn_dev")
    assert migrate_lock_name("sn_dev") != migrate_lock_name("sn_test")
    assert len(migrate_lock_name("x" * 64)) <= 64


def test_other_database_does_not_block_sync(w, api, sql, monkeypatch):
    """别的库（同一台 MySQL）占着订单同步锁：本库不显示「同步进行中」、照常同步；本库自己的锁被占时才算在同步。"""
    monkeypatch.setattr(locks, "SYNC_WAIT_SECONDS", 1)
    org = w.org(uid("O")[:10])
    a = uid("MO")
    other = _hold(locks.lock_name("sync", "orders", "sn_other_env"))
    try:
        assert orders.sync_progress() == {"running": False}
        w.order(a, org, "C1", uid("PI"), ("M1", 5))
        assert sql.count("SELECT COUNT(*) FROM sn_prd_mo WHERE bill_no = %s", a) == 1
    finally:
        other.close()
    mine = _hold(locks.lock_name("sync", "orders", DB))
    try:
        assert orders.sync_progress() == {"running": True, "mode": ""}, "本库别的进程在同步"
        assert api.post("/api/orders/sync", m(bill_no=a), w.admin).code == "ORDER_SYNC_BUSY"
    finally:
        mine.close()
    ok(api.post("/api/orders/sync", m(bill_no=a), w.admin))


def test_other_database_does_not_block_pi(w, api, sql):
    """同一 PI 号在别的库被占着（如开发库正在生成）：本库照常拿到 PI 操作锁；本库的 PI 锁照常互斥。"""
    pi = uid("PI")
    other = _hold(locks.lock_name("pi", pi, "sn_other_env"))
    try:
        held = locks.pi_lock(pi)
        assert held is not None
        try:
            assert locks.try_acquire("pi", [pi]) is None, "本库同一 PI 仍互斥"
        finally:
            held.release()
    finally:
        other.close()
