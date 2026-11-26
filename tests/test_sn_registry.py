"""防重加固（2026-11-26）：全公司 SN 唯一登记（sn_registry）与前缀入场（sn_prefix_claim）。
业务规则不变：原来能成功的照常成功、报错码不变；只是原来两边都会放行的并发 / 跨表撞号现在由数据库或入场队列拦下。"""

from __future__ import annotations

import threading
import time

import pytest
from conftest import m, ok, uid
from test_prefix_rule import P, custom, mk, sns


def reg(sql, xs) -> dict[str, int]:
    xs = list(xs)
    if not xs:
        return {}
    ph = ",".join(["%s"] * len(xs))
    return {r["sn"]: int(r["kind"]) for r in sql.all(f"SELECT sn, kind FROM sn_registry WHERE sn IN ({ph})", *xs)}


# ---------------------------------------------------------------------- 全公司 SN 唯一登记


def test_registry_follows_generate_and_delete(w, api, sql):
    """生成时登记、删号时移除；删后重新生成同样的号照常（与原来相同）。"""
    pre = P()
    _, pi, b = mk(w, ("M1", 3))
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    w.generate(pi)
    got = sns(sql, pi)
    assert reg(sql, got) == {x: 1 for x in got}
    ok(w.delete(pi, start_sn=got[1]))
    assert reg(sql, got) == {got[0]: 1, got[2]: 1}, "删掉的号移除登记"
    ok(w.delete(pi, bills=[b]))
    assert reg(sql, got) == {}
    w.generate(pi)
    assert sns(sql, pi) == got and reg(sql, got) == {x: 1 for x in got}, "删光后重新生成同样的号"


def test_registry_dual_sn(w, api, sql):
    """双 SN：第一 SN、第二 SN 都登记（kind 1 / 2），删号时一起移除。"""
    pre, pre2 = P(), P()
    _, pi, b = mk(w, ("M1", 2))
    ok(api.post("/api/generate/dual", m(pi_no=pi, dual=True), w.admin))
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    ok(
        api.post(
            "/api/rules/line-select", m(pi_no=pi, lines=[m(bill_no=b, seq=1)], custom=custom(pre2), slot=2), w.admin
        )
    )
    w.generate(pi)
    first = sns(sql, pi)
    second = [r["sn2"] for r in sql.all("SELECT sn2 FROM sn_item_sub WHERE seq_pi_no = %s ORDER BY seq_dec", pi)]
    assert len(first) == len(second) == 2
    assert reg(sql, first + second) == {**{x: 1 for x in first}, **{x: 2 for x in second}}
    ok(w.delete(pi, bills=[b]))
    assert reg(sql, first + second) == {}


def test_registry_blocks_cross_table_duplicate(w, api, sql):
    """第一 SN 与已有的第二 SN 相同（两张表的唯一键互相管不到）：由登记拦下，整次回滚，报撞号（与号池里撞号相同）。"""
    pre = P()
    _, pi, b = mk(w, ("M1", 2))
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    # 模拟已有一个与将要生成的第一个号相同的第二 SN（只有并发漏判或历史数据才会出现）
    sql.exec("INSERT INTO sn_registry(sn, kind) VALUES (%s, 2)", f"{pre}0002")
    try:
        p = ok(w.preview(pi))
        r = api.post("/api/generate/run", m(token=p["token"]), w.admin)
        assert r.code == "GEN_DUPLICATE_SN", r.body
        assert sns(sql, pi) == [] and reg(sql, [f"{pre}0001"]) == {}, "整次回滚，号池与登记都不留"
    finally:
        sql.exec("DELETE FROM sn_registry WHERE sn = %s AND kind = 2", f"{pre}0002")
    w.generate(pi)
    assert sns(sql, pi) == [f"{pre}0001", f"{pre}0002"]


# ---------------------------------------------------------------------- 前缀入场


@pytest.fixture
def short_wait(monkeypatch):
    from app.platform import locks

    monkeypatch.setattr(locks, "PREFIX_WAIT_SECONDS", 2)


def _fake_inflight(sql, prefix: str, pi: str):
    """模拟别的 PI 正在用 prefix 生成（还没提交）：在途登记 + 持有它的前缀锁。"""
    from app.platform import locks

    sql.exec(
        "INSERT INTO sn_prefix_claim(prefix, kind, pi_no, created_at) VALUES (%s,'P',%s,NOW()) "
        "ON DUPLICATE KEY UPDATE pi_no = VALUES(pi_no)",
        prefix,
        pi,
    )
    h = locks.try_acquire("prefix", [prefix], 0)
    assert h is not None
    return h


def test_entry_waits_for_inflight_overlapping_prefix(w, api, sql, short_wait):
    """别的 PI 正在用互为开头的前缀生成（未提交）：等它结束再判断；一直不结束报稍后重试（不再两边都放行）。"""
    pre = P()
    other = uid("PI")
    h = _fake_inflight(sql, pre, other)
    try:
        _, pi, b = mk(w, ("M1", 1))
        assert w.select(pi, [(b, 1)], custom=custom(pre + "X")).code == "CONFLICT_RETRY"
        # 同一前缀不受影响（共用前缀照常，由前缀锁排队）；不互为开头的前缀不受影响
        ok(w.select(pi, [(b, 1)], custom=custom(P())))
    finally:
        h.release()
    # 对方结束（回滚：没留下版本）：在途登记失效，照常放行，失效的登记顺手删掉
    ok(w.select(pi, [(b, 1)], custom=custom(pre + "X")))
    assert sql.count("SELECT COUNT(*) FROM sn_prefix_claim WHERE prefix = %s", pre) == 0


def test_entry_rechecks_after_inflight_finishes(w, api, sql, short_wait):
    """等待中对方结束：重新按最新已提交的数据判断——对方回滚时放行（与对方没生成时相同）。"""
    pre = P()
    _, pi, b = mk(w, ("M1", 1))
    h = _fake_inflight(sql, pre, uid("PI"))
    threading.Timer(0.5, h.release).start()
    t0 = time.monotonic()
    ok(w.select(pi, [(b, 1)], custom=custom(pre + "X")))
    assert time.monotonic() - t0 >= 0.4, "等到对方结束才判断"


def test_entry_inflight_generation_then_overlap(w, api, sql):
    """真实生成在途时选互为开头的前缀：等生成提交后按已提交的版本判断，报前缀冲突（与生成先提交时相同）；
    生成结束后在途登记已删。"""
    pre = P()
    _, p1, b1 = mk(w, ("M1", 15000))
    ok(w.select(p1, [(b1, 1)], custom=custom(pre, seq_len=6)))
    p = ok(w.preview(p1))
    j = ok(api.post("/api/generate/run", m(token=p["token"]), w.admin))
    _, p2, b2 = mk(w, ("M1", 1))
    r = w.select(p2, [(b2, 1)], custom=custom(pre + "X"))
    for _ in range(300):
        if sql.scalar("SELECT status FROM sn_gen_job WHERE gen_batch = %s", j["gen_batch"]) != "RUNNING":
            break
        time.sleep(0.1)
    assert sql.scalar("SELECT status FROM sn_gen_job WHERE gen_batch = %s", j["gen_batch"]) == "SUCCESS"
    assert r.code == "RULE_PREFIX_OVERLAP", r.body
    assert sql.count("SELECT COUNT(*) FROM sn_prefix_claim WHERE prefix = %s", pre) == 0, "生成结束删掉在途登记"


def test_claim_rules_match_prefix_rules():
    """在途前缀之间的冲突与前缀规则相同：不同前缀互为开头冲突；相同的普通前缀是共用、相同的识别码前缀层各取各的值，
    普通前缀与识别码前缀层相同冲突。"""
    from app.modules.sn.services import rules

    P_, I_ = rules.CLAIM_PLAIN, rules.CLAIM_INC
    assert rules._claims_conflict(P_, "AB", P_, "ABC") and rules._claims_conflict(P_, "ABC", P_, "AB")
    assert not rules._claims_conflict(P_, "AB", P_, "AB")
    assert not rules._claims_conflict(I_, "AB", I_, "AB")
    assert rules._claims_conflict(P_, "AB", I_, "AB") and rules._claims_conflict(I_, "AB", P_, "ABX")
    assert not rules._claims_conflict(P_, "AB", P_, "AC")
