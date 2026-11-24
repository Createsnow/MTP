"""附件项次组件（2026-11-24）：SN = 前缀 + 附件项次（定长，左侧补 0）+ 固定字符 + 流水 + …。

附件项次取每个物料行的附件项次（与号上的 attach_no 相同），前缀不变（一张 PI 一个前缀，流水按前缀接号），
所以号仍由「前缀 + 流水」唯一确定；同一前缀下附件项次位数、固定字符锁定；项次比位数长的物料行不能生成。
不带附件项次的规则、MES 报文字段都与原来相同。"""

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


def custom(prefix: str, width: int = 2, tail: str = "-", **kv) -> dict:
    return m(prefix=prefix, base=10, seq_len=4, with_length=False, attach_width=width, attach_tail=tail, **kv)


def sns(sql, pi: str) -> list[tuple[str, str]]:
    return [
        (r["sn"], r["attach_no"])
        for r in sql.all("SELECT sn, attach_no FROM sn_item WHERE seq_pi_no = %s ORDER BY seq_dec", pi)
    ]


def test_attach_between_prefix_and_serial_follows_each_line(w, api, sql):
    """每个物料行的号写入自己的附件项次（补 0 到 2 位），流水按前缀接号；版本记下附件项次位数与固定字符；
    MES 推送明细字段不变，sn 即完整 SN、attachNo 与 SN 里的项次一致。"""
    org = w.org(uid("O")[:10])
    w.enable_mes(org)
    _, pi, bill = mk(w, ("M1", 2, "100", "10"), ("M2", 2, "100", "3"), org=org)
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p)))
    pv = ok(w.preview(pi))
    assert pv["generatable"]
    # 排号先按附件项次从小到大：项次 3 的行先拿流水 1、2
    got = {(x["seq"], x["start_sn"], x["end_sn"]) for x in pv["items"]}
    assert got == {(2, f"{p}03-0001", f"{p}03-0002"), (1, f"{p}10-0003", f"{p}10-0004")}
    w.generate(pi)
    assert sns(sql, pi) == [(f"{p}03-0001", "3"), (f"{p}03-0002", "3"), (f"{p}10-0003", "10"), (f"{p}10-0004", "10")]
    v = sql.one("SELECT * FROM sn_rule_version WHERE prefix = %s AND claimed_pi = %s", p, pi)
    assert (v["attach_width"], v["attach_tail"]) == (2, "-")
    assert sql.one("SELECT seq_prefix FROM sn_item WHERE seq_pi_no = %s LIMIT 1", pi)["seq_prefix"] == p, "前缀不变"
    w.allocate(pi)
    w.push()
    batch = next(x["batch"] for x in MES.received if x["batch"]["batchType"] == "NEW" and x["batch"]["pi"] == pi)
    items = {x["sn"]: x for x in batch["items"]}
    assert items[f"{p}10-0003"]["attachNo"] == "10" and items[f"{p}03-0001"]["attachNo"] == "3"
    assert items[f"{p}10-0003"]["serial"] == "0003" and items[f"{p}10-0003"]["serialDec"] == "3"


def test_attach_too_long_blocks_only_that_line(w, api, sql):
    """附件项次比位数长的物料行不能生成（ATTACH_TOO_LONG），别的行照常；调大位数（未生成过号）后可以生成。"""
    _, pi, bill = mk(w, ("M1", 1, "100", "1"), ("M2", 1, "100", "123"))
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p, width=2)))
    pv = ok(w.preview(pi))
    lines = ok(api.get(f"/api/generate/context?pi={pi}", w.admin)).body["bills"][0]["lines"]
    assert "ATTACH_TOO_LONG" in next(x for x in lines if x["seq"] == 2)["issues"]
    assert "ATTACH_TOO_LONG" not in next(x for x in lines if x["seq"] == 1)["issues"]
    assert not pv["generatable"], "整张 PI 预演含不能生成的行"
    w.generate(pi, lines=[(bill, 1)])
    assert sns(sql, pi) == [(f"{p}01-0001", "1")]


def test_attach_format_locked_under_prefix(w, api, sql):
    """同一前缀下附件项次位数、固定字符锁定：别的 PI 共用这个前缀时不带附件项次或位数不同都不能用（须换新前缀），
    相同格式可以共用、流水接在共同的最大号之后。"""
    org = w.org(uid("O")[:10])
    _, pi, bill = mk(w, ("M1", 2, "100", "1"), org=org)
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p)))
    w.generate(pi)
    _, p2, b2 = mk(w, ("M1", 1, "100", "1"), org=org)
    for bad in (m(prefix=p, base=10, seq_len=4, with_length=False), custom(p, width=3), custom(p, tail="_")):
        r = w.select(p2, [(b2, 1)], custom=bad)
        assert r.code == "RULE_PREFIX_ATTACH_LOCKED", str(bad)
        assert r.body["params"]["used"] == "附件项次（2 位） + -"
    # 流水部分不同仍报原来的错误码
    assert w.select(p2, [(b2, 1)], custom=custom(p) | m(seq_len=5)).code == "RULE_PREFIX_FORMAT_LOCKED"
    ok(w.select(p2, [(b2, 1)], custom=custom(p)))
    w.generate(p2)
    assert sns(sql, p2) == [(f"{p}01-0003", "1")]


def test_query_delete_and_unit_with_attach(w, api, sql):
    """带附件项次的号：SN 查询按完整 SN 起止流水筛选、产品单位按定长附件项次段截出；按 SN 起止号删号。"""
    _, pi, bill = mk(w, ("M1", 3, "100", "7", "PCS"))
    p = P()
    ok(w.select(pi, [(bill, 1)], custom=custom(p, width=3, tail="", with_unit=True)))
    w.generate(pi)
    assert [s for s, _ in sns(sql, pi)] == [f"{p}0070001PCS", f"{p}0070002PCS", f"{p}0070003PCS"]
    q = ok(api.get(f"/api/sn?pi={pi}&seq_from={p}0070002PCS&seq_to={p}0070003PCS", w.admin))
    assert [x["sn"] for x in q["items"]] == [f"{p}0070002PCS", f"{p}0070003PCS"]
    assert {x["unit"] for x in q["items"]} == {"PCS"}
    ok(w.delete(pi, start_sn=f"{p}0070003PCS", end_sn=f"{p}0070003PCS", reason="x"))
    assert [s for s, _ in sns(sql, pi)] == [f"{p}0070001PCS", f"{p}0070002PCS"]


def test_rule_without_attach_unchanged(w, api, sql):
    """不带附件项次：选规则登记的参数没有 attach_* 字段，版本 attach_width = 0，号与原来相同。"""
    _, pi, bill = mk(w, ("M1", 1, "100", "5"))
    p = P()
    r = ok(w.select(pi, [(bill, 1)], custom=m(prefix=p, base=10, seq_len=4, with_length=False)))
    assert "attach_width" not in (r["rule"]["custom"] or {})
    w.generate(pi)
    assert sns(sql, pi) == [(f"{p}0001", "5")]
    v = sql.one("SELECT attach_width, attach_tail FROM sn_rule_version WHERE prefix = %s", p)
    assert (v["attach_width"], v["attach_tail"]) == (0, "")
