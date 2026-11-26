"""PI 全部删号后可自定义规则（2026-11-26）：本 PI 的前缀已腾空（号全删掉、没有别的 PI 用过或登记过）时，不论原来是
普通规则（含附件项次）还是识别码，都可以按自定义改：普通 ↔ 识别码、改附件项次、改流水位数、改识别码位数 / 范围 /
固定字符、改用与旧前缀互为开头的前缀。一个前缀一条规则：规则的当前版本跟着换成本 PI 这次用的。
有别的 PI 用过或登记过时照常锁定（前缀互为开头、格式、识别码位数）。"""

from __future__ import annotations

import pytest
from conftest import m, ok, uid
from test_inc_prefix import C, H, inc, mk, sns

ATT = {"with_attach": True, "attach_head": "-", "attach_tail": "-"}


def plain(p: str, seq_len: int = 4, **kv) -> dict:
    return m(prefix=p, base=10, seq_len=seq_len, with_length=False, **kv)


#: 原规则 → 删光号后改成的规则 → 第一个号
CASES = {
    "附件项次→不带": (lambda h: plain(h, **ATT), lambda h: plain(h), lambda h: f"{h}0001"),
    "普通→带附件项次": (lambda h: plain(h), lambda h: plain(h, **ATT), lambda h: f"{h}-1-0001"),
    "附件项次→识别码": (lambda h: plain(h, **ATT), lambda h: inc(h), lambda h: f"{h}00001"),
    "普通→更长识别码": (lambda h: plain(h), lambda h: inc(h + "Z"), lambda h: f"{h}Z00001"),
    "普通→更短识别码": (lambda h: plain(h), lambda h: inc(h[:-1]), lambda h: f"{h[:-1]}00001"),
    "识别码→普通": (lambda h: inc(h), lambda h: plain(h), lambda h: f"{h}0001"),
    "识别码→附件项次": (lambda h: inc(h), lambda h: plain(h, **ATT), lambda h: f"{h}-1-0001"),
    "识别码→更长普通": (lambda h: inc(h), lambda h: plain(h + "Z"), lambda h: f"{h}Z0001"),
    "识别码→更短普通": (lambda h: inc(h), lambda h: plain(h[:-1]), lambda h: f"{h[:-1]}0001"),
    "识别码加附件项次": (lambda h: inc(h), lambda h: inc(h, **ATT), lambda h: f"{h}0-1-0001"),
    "识别码去附件项次": (lambda h: inc(h, **ATT), lambda h: inc(h), lambda h: f"{h}00001"),
    "识别码改位数": (lambda h: inc(h), lambda h: inc(h, end=99), lambda h: f"{h}000001"),
    "识别码改流水位数": (lambda h: inc(h), lambda h: inc(h, seq_len=6), lambda h: f"{h}0000001"),
    "识别码改固定字符": (lambda h: inc(h), lambda h: inc(h, tail="T"), lambda h: f"{h}0T0001"),
    "识别码改起始值": (lambda h: inc(h), lambda h: inc(h, start=2), lambda h: f"{h}20001"),
    "识别码→更长识别码": (lambda h: inc(h), lambda h: inc(h + "Z"), lambda h: f"{h}Z00001"),
    "识别码→更短识别码": (lambda h: inc(h), lambda h: inc(h[:-1]), lambda h: f"{h[:-1]}00001"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_custom_after_all_deleted(w, sql, name):
    a, b, first = CASES[name]
    h = H()
    _, pi, bill = mk(w, ("M1", 2), customer=C())
    ok(w.select(pi, [(bill, 1)], custom=a(h)))
    w.generate(pi)
    ok(w.delete(pi, bills=[bill]))
    ok(w.select(pi, [(bill, 1)], custom=b(h)))
    w.generate(pi)
    assert sns(sql, pi)[0] == first(h)


def _cur(sql, head: str) -> tuple[dict, dict]:
    r = sql.one("SELECT * FROM sn_rule WHERE prefix = %s", head)
    v = sql.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = %s", r["id"], r["current_version"])
    return r, v


def test_inc_to_plain_converts_rule_and_others_share(w, api, sql):
    """识别码 → 同前缀普通规则：仍是一条规则，当前版本换成普通版本、清掉适用客户，规则库不再当识别码规则；
    别的 PI 选这条规则共用前缀、流水接续；同前缀的识别码不能再用。"""
    org = w.org(uid("O")[:10])
    cust = C()
    h = H()
    _, pi, b = mk(w, ("M1", 2), org=org, customer=cust)
    ok(w.select(pi, [(b, 1)], custom=inc(h)))
    w.generate(pi)
    ok(w.delete(pi, bills=[b]))
    ok(w.select(pi, [(b, 1)], custom=plain(h)))
    w.generate(pi)
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE prefix = %s", h) == 1
    r, v = _cur(sql, h)
    assert (v["prefix"], v["inc_head"], v["claimed_pi"], r["inc_customers"]) == (h, None, pi, None)
    assert api.put(f"/api/rules/{r['id']}/inc-customers", {"customers": [cust]}, w.admin).code == "RULE_NOT_INC"
    _, p2, b2 = mk(w, ("M1", 1), org=org, customer=cust)
    ok(w.select(p2, [(b2, 1)], rule_id=r["id"]))
    w.generate(p2)
    assert sns(sql, p2) == [f"{h}0003"], "共用前缀，流水接续"
    _, p3, b3 = mk(w, ("M1", 1), org=org, customer=cust)
    assert w.select(p3, [(b3, 1)], custom=inc(h)).code == "RULE_PREFIX_OVERLAP"


def test_round_trip_plain_inc_plain_inc(w, sql):
    """同一 PI 删光号后来回改：普通 → 识别码 → 普通 → 识别码，每次都按这次的规则生成，始终一条规则。"""
    h = H()
    _, pi, b = mk(w, ("M1", 1), customer=C())
    for c, want in (
        (plain(h, **ATT), f"{h}-1-0001"),
        (inc(h), f"{h}00001"),
        (plain(h), f"{h}0001"),
        (inc(h, end=99), f"{h}000001"),
    ):
        ok(w.select(pi, [(b, 1)], custom=c))
        w.generate(pi)
        assert sns(sql, pi) == [want]
        ok(w.delete(pi, bills=[b]))
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE prefix = %s", h) == 1


def test_inc_width_change_then_other_pi_follows_new_width(w, sql):
    """前缀层腾空时本 PI 改识别码位数：之后同客户的 PI 按新位数取下一个值，按旧位数报 RULE_INC_WIDTH。"""
    org = w.org(uid("O")[:10])
    cust = C()
    h = H()
    _, p1, b1 = mk(w, ("M1", 1), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(h)))
    w.generate(p1)
    ok(w.delete(p1, bills=[b1]))
    ok(w.select(p1, [(b1, 1)], custom=inc(h, end=99)))
    w.generate(p1)
    _r, v = _cur(sql, h)
    assert (v["inc_end"], v["claimed_pi"]) == (99, p1), "当前版本换成新配置"
    _, p2, b2 = mk(w, ("M1", 1), org=org, customer=cust)
    assert w.select(p2, [(b2, 1)], custom=inc(h)).code == "RULE_INC_WIDTH"
    ok(w.select(p2, [(b2, 1)], custom=inc(h, end=99)))
    w.generate(p2)
    assert sns(sql, p2) == [f"{h}010001"]


def test_not_released_when_other_pi_on_inc_head(w, sql):
    """前缀层下还有别的 PI（取过值，或只选了规则还没生成）：本 PI 删光号后照常锁定。"""
    org = w.org(uid("O")[:10])
    cust = C()
    h = H()
    _, p1, b1 = mk(w, ("M1", 1), org=org, customer=cust)
    _, p2, b2 = mk(w, ("M1", 1), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(h)))
    w.generate(p1)
    # 别的 PI 只选了规则（预显示下一个值）
    ok(w.select(p2, [(b2, 1)], custom=inc(h)))
    ok(w.delete(p1, bills=[b1]))
    assert w.select(p1, [(b1, 1)], custom=plain(h)).code == "RULE_PREFIX_OVERLAP"
    assert w.select(p1, [(b1, 1)], custom=inc(h + "Z")).code == "RULE_PREFIX_OVERLAP"
    # 别的 PI 取过值（号还在）
    w.generate(p2)
    assert w.select(p1, [(b1, 1)], custom=plain(h)).code == "RULE_PREFIX_OVERLAP"
    assert w.select(p1, [(b1, 1)], custom=plain(h[:-1])).code == "RULE_PREFIX_OVERLAP"
    assert w.select(p1, [(b1, 1)], custom=inc(h, seq_len=6)).code == "RULE_PREFIX_FORMAT_LOCKED"
    assert w.select(p1, [(b1, 1)], custom=inc(h, **ATT)).code == "RULE_PREFIX_ATTACH_LOCKED"
    assert w.select(p1, [(b1, 1)], custom=inc(h, end=99)).code == "RULE_INC_WIDTH", "识别码位数锁定"
    # 原规则照常能用
    ok(w.select(p1, [(b1, 1)], custom=inc(h)))
    w.generate(p1)
    assert sns(sql, p1) == [f"{h}00001"]


def test_abandoned_inc_head_does_not_block_sharing_new_prefix(w, sql):
    """本 PI 识别码前缀层腾空后改用与它互为开头的普通前缀：旧前缀层没有号、没有 PI 登记（已废弃），不挡别的 PI
    共用新前缀；别的 PI 要用旧前缀层的识别码，与还在用的新前缀互为开头，照常挡住。"""
    org = w.org(uid("O")[:10])
    cust = C()
    h = H()
    _, p1, b1 = mk(w, ("M1", 1), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(h)))
    w.generate(p1)
    ok(w.delete(p1, bills=[b1]))
    ok(w.select(p1, [(b1, 1)], custom=plain(h + "Z")))
    w.generate(p1)
    _, p2, b2 = mk(w, ("M1", 1), org=org, customer=cust)
    ok(w.select(p2, [(b2, 1)], custom=plain(h + "Z")))
    w.generate(p2)
    assert sns(sql, p2) == [f"{h}Z0002"]
    _, p3, b3 = mk(w, ("M1", 1), org=org, customer=cust)
    assert w.select(p3, [(b3, 1)], custom=inc(h)).code == "RULE_PREFIX_OVERLAP"
