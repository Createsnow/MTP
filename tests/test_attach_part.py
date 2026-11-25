"""附件项次组件（2026-11-24）：SN = 前缀 + 前连接符 + 附件项次（原样，不补 0）+ 后连接符 + 流水 + …。

附件项次取每个物料行的附件项次（与号上的 attach_no 相同），前缀不变（一张 PI 一个前缀，流水按前缀接号），号仍由
「前缀 + 流水」唯一确定；前后连接符默认 -、都可以不填（后连接符不填时页面提示可能重号，撞上时整次生成回滚）；
同一前缀下带不带附件项次、前后连接符锁定。不带附件项次的规则、MES 报文字段都与原来相同。"""

from __future__ import annotations

from conftest import MES, m, ok, uid


def P() -> str:
    """各用例唯一的前缀（不与别的用例的前缀一个是另一个的开头）。"""
    return "J" + uid("").replace("-", "") + "Z"


def mk(w, *lines, org=None):
    org = org or w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, *lines)
    return org, pi, bill


def custom(prefix: str, head: str = "-", tail: str = "-", **kv) -> dict:
    return m(
        prefix=prefix, base=10, seq_len=4, with_length=False, with_attach=True, attach_head=head, attach_tail=tail, **kv
    )


def sns(sql, pi: str) -> list[tuple[str, str]]:
    return [
        (r["sn"], r["attach_no"])
        for r in sql.all("SELECT sn, attach_no FROM sn_item WHERE seq_pi_no = %s ORDER BY seq_dec", pi)
    ]


def test_attach_as_is_between_connectors(w, api, sql):
    """每个物料行的号写入自己的附件项次（原样：1 就是 1，10 就是 10，100 就是 100），前后各一个连接符；流水按前缀接号；
    版本记下连接符；MES 推送明细字段不变，sn 即完整 SN、attachNo 与 SN 里的项次一致。"""
    org = w.org(uid("O")[:10])
    w.enable_mes(org)
    _, pi, bill = mk(w, ("M1", 1, "100", "100"), ("M2", 1, "100", "1"), ("M3", 1, "100", "10"), org=org)
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p)))
    w.generate(pi)
    # 排号先按附件项次从小到大
    assert sns(sql, pi) == [(f"{p}-1-0001", "1"), (f"{p}-10-0002", "10"), (f"{p}-100-0003", "100")]
    v = sql.one("SELECT * FROM sn_rule_version WHERE prefix = %s AND claimed_pi = %s", p, pi)
    assert (v["with_attach"], v["attach_head"], v["attach_tail"]) == (1, "-", "-")
    assert sql.one("SELECT seq_prefix FROM sn_item WHERE seq_pi_no = %s LIMIT 1", pi)["seq_prefix"] == p, "前缀不变"
    w.allocate(pi)
    w.push()
    batch = next(x["batch"] for x in MES.received if x["batch"]["batchType"] == "NEW" and x["batch"]["pi"] == pi)
    items = {x["sn"]: x for x in batch["items"]}
    assert items[f"{p}-100-0003"]["attachNo"] == "100" and items[f"{p}-1-0001"]["attachNo"] == "1"
    assert items[f"{p}-100-0003"]["serial"] == "0003" and items[f"{p}-100-0003"]["serialDec"] == "3"


def test_connectors_optional(w, api, sql):
    """前后连接符都可以不填（后连接符不填时只在页面提示可能重号，不拦）；只填后连接符也可以。"""
    _, pi, bill = mk(w, ("M1", 1, "100", "3"), ("M2", 1, "100", "12"))
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p, head="", tail="")))
    w.generate(pi)
    assert sns(sql, pi) == [(f"{p}30001", "3"), (f"{p}120002", "12")]
    _, p2, b2 = mk(w, ("M1", 1, "100", "3"))
    q = P()
    ok(w.select(p2, [(b2, 1)], custom=custom(q, head="", tail="_")))
    w.generate(p2)
    assert sns(sql, p2) == [(f"{q}3_0001", "3")]


def test_attach_format_locked_under_prefix(w, api, sql):
    """同一前缀下带不带附件项次、前后连接符锁定：别的 PI 共用这个前缀时不带附件项次或连接符不同都不能用（须换新前缀），
    相同格式可以共用、流水接在共同的最大号之后。"""
    org = w.org(uid("O")[:10])
    _, pi, bill = mk(w, ("M1", 2, "100", "1"), org=org)
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p)))
    w.generate(pi)
    _, p2, b2 = mk(w, ("M1", 1, "100", "1"), org=org)
    used = "前连接符「-」+ 附件项次 + 后连接符「-」"
    for bad in (m(prefix=p, base=10, seq_len=4, with_length=False), custom(p, head=""), custom(p, tail="_")):
        r = w.select(p2, [(b2, 1)], custom=bad)
        assert r.code == "RULE_PREFIX_ATTACH_LOCKED", str(bad)
        assert r.body["params"]["used"] == used
    # 流水部分不同仍报原来的错误码
    assert w.select(p2, [(b2, 1)], custom=custom(p) | m(seq_len=5)).code == "RULE_PREFIX_FORMAT_LOCKED"
    ok(w.select(p2, [(b2, 1)], custom=custom(p)))
    w.generate(p2)
    assert sns(sql, p2) == [(f"{p}-1-0003", "1")]


def test_query_delete_and_unit_with_attach(w, api, sql):
    """带附件项次的号：SN 查询按完整 SN 起止流水筛选、产品单位按号上的项次截出；按 SN 起止号删号。"""
    _, pi, bill = mk(w, ("M1", 3, "100", "7", "PCS"))
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p, head="", tail="-", with_unit=True)))
    w.generate(pi)
    assert [s for s, _ in sns(sql, pi)] == [f"{p}7-0001PCS", f"{p}7-0002PCS", f"{p}7-0003PCS"]
    q = ok(api.get(f"/api/sn?pi={pi}&seq_from={p}7-0002PCS&seq_to={p}7-0003PCS", w.admin))
    assert [x["sn"] for x in q["items"]] == [f"{p}7-0002PCS", f"{p}7-0003PCS"]
    assert {x["unit"] for x in q["items"]} == {"PCS"}
    ok(w.delete(pi, start_sn=f"{p}7-0003PCS", end_sn=f"{p}7-0003PCS", reason="x"))
    assert [s for s, _ in sns(sql, pi)] == [f"{p}7-0001PCS", f"{p}7-0002PCS"]


def test_rule_without_attach_unchanged(w, api, sql):
    """不带附件项次：选规则登记的参数没有附件项次字段，版本不带附件项次，号与原来相同。"""
    _, pi, bill = mk(w, ("M1", 1, "100", "5"))
    p = P()
    r = ok(w.select(pi, [(bill, 1)], custom=m(prefix=p, base=10, seq_len=4, with_length=False)))
    assert "with_attach" not in (r["rule"]["custom"] or {})
    w.generate(pi)
    assert sns(sql, pi) == [(f"{p}0001", "5")]
    v = sql.one("SELECT with_attach, attach_head, attach_tail FROM sn_rule_version WHERE prefix = %s", p)
    assert (v["with_attach"], v["attach_head"], v["attach_tail"]) == (0, "", "")
