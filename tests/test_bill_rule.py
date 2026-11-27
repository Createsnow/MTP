"""按单据选规则（2026-11-27 变更）：一个 PI 下可以有多个规则前缀，排号逻辑不变（按前缀共用一套流水）。
- 选规则按勾选的单据（勾选的物料行作用于所在的整张单据）；本 PI 第一次选规则时登记为 PI 默认规则，勾选本 PI 全部单据时
  改默认规则（沿用旧默认的单据先固化为单据规则），其余写单据规则；
- 没有单据规则的单据（含之后同步进来的单据）沿用 PI 默认规则，新增物料行跟随所在单据；
- 单据有号时前缀锁定（RULE_BILL_PREFIX_LOCKED），删号不动单据规则，号删光后可改选；也可改用 PI 默认规则；
- 一次生成只能有一个前缀（GEN_MULTI_PREFIX）；
- 同一 PI、同一物料行里可以有流水相同、前缀不同的号：删号、转移、查询按 SN 起止号时只取同一前缀；
- 第二 SN 与第一 SN 逻辑相同；单据在金蝶改了 PI 且没号时，沿用新 PI 的默认规则。MES 报文不变。"""

from __future__ import annotations

from conftest import BILLS, m, ok, uid
from test_line_void import set_rule


def P() -> str:
    return "B" + uid("").replace("-", "")


def C(prefix: str, seq_len: int = 4) -> dict:
    """自定义规则：前缀 + 十进制流水（不带米数）。"""
    return {"prefix": prefix, "base": 10, "seq_len": seq_len, "with_length": False}


def mk(w, *lines, pi=None, org=None, bill=None):
    org = org or w.org(uid("O")[:10])
    pi = pi or uid("PI")
    bill = bill or uid("MO")
    w.order(bill, org, "C1", pi, *lines)
    return org, pi, bill


def sns(sql, bill: str) -> list[str]:
    return [r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE bill_no = %s ORDER BY seq_prefix, seq_dec", bill)]


def pi_reg(sql, pi: str, table: str = "sn_pi_rule") -> dict | None:
    return sql.one(f"SELECT * FROM {table} WHERE pi_no = %s", pi)


def bill_reg(sql, bill: str, table: str = "sn_bill_rule") -> dict | None:
    return sql.one(f"SELECT * FROM {table} WHERE bill_no = %s", bill)


def ctx(api, w, pi: str) -> dict:
    return ok(api.get(f"/api/generate/context?pi={pi}", w.admin)).body


def view(api, w, pi: str, bill: str) -> dict:
    return next(b for b in ctx(api, w, pi)["bills"] if b["bill_no"] == bill)["rule_view"]


def test_bills_use_different_prefixes_numbering_by_prefix(w, api, sql):
    """单据 A 用 X 出 X0001～X0003；单据 B 选新前缀 Y，从 Y0001 开始；单据 C 选 X，接着出 X0004。
    同一 PI 下 X0001 与 Y0001 的流水都是 1（流水归属 PI + 流水不再唯一，同前缀下流水唯一照旧）。"""
    org, pi, a = mk(w, ("M1", 3))
    _, _, b = mk(w, ("M1", 2), pi=pi, org=org)
    _, _, c = mk(w, ("M1", 1), pi=pi, org=org)
    x, y = P(), P()
    r = ok(w.select(pi, [(a, 1)], custom=C(x)))
    assert r["layer"] == "PI", "本 PI 第一次选规则：登记为 PI 默认规则"
    assert pi_reg(sql, pi)["prefix"] == x and bill_reg(sql, a) is None
    w.generate(pi, bills=[a])
    assert sns(sql, a) == [f"{x}0001", f"{x}0002", f"{x}0003"]
    r = ok(w.select(pi, [(b, 1)], custom=C(y)))
    assert (r["layer"], r["lines"]) == ("BILL", 1)
    assert bill_reg(sql, b)["prefix"] == y and pi_reg(sql, pi)["prefix"] == x, "默认规则不变"
    w.generate(pi, bills=[b])
    assert sns(sql, b) == [f"{y}0001", f"{y}0002"], "新前缀从自己的流水开始"
    # C 沿用默认规则 X：接着 X 的最大号
    assert view(api, w, pi, c)["layer"] == "PI"
    w.generate(pi, bills=[c])
    assert sns(sql, c) == [f"{x}0004"]
    assert sql.count("SELECT COUNT(*) FROM sn_item WHERE seq_pi_no = %s AND seq_dec = 1", pi) == 2
    v = view(api, w, pi, b)
    assert (v["layer"], v["prefix"], v["locked_prefix"]) == ("BILL", y, y)
    assert ctx(api, w, pi)["prefix_gaps"][y]["next_seq"] == 3


def test_one_prefix_per_generation(w, api, sql):
    """勾选的单据前缀不同不能一起生成（GEN_MULTI_PREFIX）；前缀相同的照常；已生成完的单据不算。"""
    org, pi, a = mk(w, ("M1", 2))
    _, _, b = mk(w, ("M1", 2), pi=pi, org=org)
    _, _, c = mk(w, ("M1", 1), pi=pi, org=org)
    x, y = P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    res = w.preview(pi, bills=[a, b])
    assert res.code == "GEN_MULTI_PREFIX", str(res)
    assert x in res.body["message"] and y in res.body["message"]
    assert w.api.get(f"/api/generate/gaps?pi={pi}&bills={a}&bills={b}", w.admin).code == "GEN_MULTI_PREFIX"
    w.generate(pi, bills=[a, c])
    # A 已生成完：和 B 一起勾选时只按 B 的前缀
    w.generate(pi, bills=[a, b])
    assert sns(sql, b) == [f"{y}0001", f"{y}0002"]


def test_new_bill_and_new_line_follow_rules(w, api, sql):
    """同步新增的单据沿用 PI 默认规则（可直接生成，也可改选）；单据新增的物料行跟随所在单据的规则。"""
    org, pi, a = mk(w, ("M1", 1))
    _, _, b = mk(w, ("M1", 1), pi=pi, org=org)
    x, y = P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    w.generate(pi, bills=[a])
    w.generate(pi, bills=[b])
    # 新单据 D：沿用默认规则 X
    _, _, d = mk(w, ("M1", 2), pi=pi, org=org)
    assert view(api, w, pi, d)["layer"] == "PI"
    w.generate(pi, bills=[d])
    assert sns(sql, d) == [f"{x}0002", f"{x}0003"]
    # 单据 B 在金蝶加了一行：跟随 B 的规则 Y 接着编
    w.order(b, org, "C1", pi, ("M1", 1), ("M2", 2, "100", "2"))
    w.generate(pi, lines=[(b, 2)])
    assert [
        r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE bill_no = %s AND bill_seq = 2 ORDER BY seq_dec", b)
    ] == [
        f"{y}0002",
        f"{y}0003",
    ]


def test_bill_prefix_lock_delete_and_use_default(w, api, sql):
    """单据有号时前缀锁定（只能选同前缀的规则）；删号不动单据规则、号删光后可改选；可改用 PI 默认规则。"""
    org, pi, a = mk(w, ("M1", 1))
    _, _, b = mk(w, ("M1", 2), pi=pi, org=org)
    x, y, z = P(), P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    w.generate(pi, bills=[b])
    res = w.select(pi, [(b, 1)], custom=C(z))
    assert res.code == "RULE_BILL_PREFIX_LOCKED", str(res)
    assert res.body["params"]["bill_no"] == b
    use_default = m(pi_no=pi, lines=[m(bill_no=b, seq=1)], use_default=True)
    assert api.post("/api/rules/line-select", use_default, w.admin).code == "RULE_BILL_PREFIX_LOCKED"
    # 同前缀（改后缀）可以
    ok(w.select(pi, [(b, 1)], custom={**C(y), "suffix": "S"}))
    # 勾选的单据里有锁定的：换别的前缀不行（A 没号，B 锁在 Y）
    assert w.select(pi, [(a, 1), (b, 1)], custom=C(z)).code == "RULE_BILL_PREFIX_LOCKED"
    ok(w.delete(pi, bills=[b], reason="t"))
    assert bill_reg(sql, b)["prefix"] == y, "删号不动单据规则"
    r = ok(api.post("/api/rules/line-select", use_default, w.admin))
    assert (r["layer"], bill_reg(sql, b)) == ("DEFAULT", None), "改用 PI 默认规则：清掉单据规则"
    assert view(api, w, pi, b)["layer"] == "PI"


def test_full_tick_changes_default_and_partial_does_not(w, api, sql):
    """只勾一部分单据不改 PI 默认规则；勾选本 PI 全部单据改默认规则，这些单据的单据规则清掉（沿用新默认）。"""
    org, pi, a = mk(w, ("M1", 1))
    _, _, b = mk(w, ("M1", 1), pi=pi, org=org)
    x, y, z = P(), P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    assert pi_reg(sql, pi)["prefix"] == x
    r = ok(w.select(pi, [(a, 1), (b, 1)], custom=C(z)))
    assert r["layer"] == "PI" and pi_reg(sql, pi)["prefix"] == z
    assert bill_reg(sql, a) is None and bill_reg(sql, b) is None
    # 再单独给 A 选 X：A 单据规则，B 仍沿用 Z
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    assert (view(api, w, pi, a)["prefix"], view(api, w, pi, b)["prefix"]) == (x, z)


def test_default_change_pins_followers(w, api, sql):
    """改 PI 默认规则前，沿用旧默认规则、这次没勾选的单据（全部物料行都失效、勾选不了的单据）先固化为单据规则。"""
    org, pi, a = mk(w, ("M1", 1))
    _, _, b = mk(w, ("M1", 1), pi=pi, org=org)
    x, z = P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    # B 的物料行失效（结案 + 未领料，按默认的失效条件）：不在「本 PI 全部单据」里
    ok(set_rule(w))
    line = BILLS[b]["TreeEntity"][0]
    line["Status"], line["PickMtrlStatus"] = "6", "1"
    w.touch(b)
    ok(w.api.post("/api/orders/sync", m(bill_no=b), w.admin))
    r = ok(w.select(pi, [(a, 1)], custom=C(z)))
    assert r["layer"] == "PI" and pi_reg(sql, pi)["prefix"] == z
    assert bill_reg(sql, b)["prefix"] == x, "沿用旧默认规则的单据固化为单据规则"
    assert bill_reg(sql, a) is None


def test_same_serial_other_prefix_on_one_line(w, api, sql):
    """同一 PI 内转单后，同一物料行上有流水相同、前缀不同的号：按 SN 起止号删号、转移、查询只取同一前缀的号。"""
    org, pi, a = mk(w, ("M1", 3))
    _, _, b = mk(w, ("M1", 3), pi=pi, org=org)
    x, y = P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    w.generate(pi, bills=[a])
    w.generate(pi, bills=[b])
    w.allocate(pi)
    body = m(pi_no=pi, bill_no=a, scope="MATERIAL", material_code="M1", to_pi=pi, to_bill=b, to_factory=org)
    ok(api.post("/api/transfers/bill", body | m(to_material="M1", reason="并单"), w.admin))
    assert len(sns(sql, b)) == 6
    # 查询：流水范围按 SN 时只看这个前缀
    q = ok(api.get(f"/api/sn?pi={pi}&seq_from={y}0001&seq_to={y}0002", w.admin))
    assert sorted(x_["sn"] for x_ in q["items"]) == [f"{y}0001", f"{y}0002"]
    assert q["range"]["expected"] == 2 and q["range"]["missing"] == 0
    assert api.get(f"/api/sn?pi={pi}&seq_from={x}0001&seq_to={y}0002", w.admin).code == "QUERY_RANGE_INVALID"
    # 转移：按号段只转 X0001～X0002（Y0001、Y0002 流水相同，不在范围内）
    _, pi2, c = mk(w, ("M1", 5), org=org)
    rng = m(pi_no=pi, bill_no=b, scope="RANGE", material_code="M1", start_sn=f"{x}0001", end_sn=f"{x}0002")
    rng |= m(to_pi=pi2, to_bill=c, to_factory=org, to_material="M1", reason="拆单")
    ok(api.post("/api/transfers/bill", rng, w.admin))
    assert sorted(sns(sql, c)) == [f"{x}0001", f"{x}0002"]
    # 删号：按 SN 起止号只删 Y0001～Y0002
    ok(w.delete(pi, start_sn=f"{y}0001", end_sn=f"{y}0002", reason="t"))
    assert sorted(sns(sql, b)) == sorted([f"{x}0003", f"{y}0003"])
    assert w.delete(pi, start_sn=f"{x}0003", end_sn=f"{y}0003").code == "DELETE_RANGE_INVALID", "起止号前缀不同"
    # 号段：按前缀分开
    seg = ok(api.get(f"/api/generate/segments?pi={pi}&bill_no={b}", w.admin))
    assert sorted((s["seq_prefix"], s["start_seq"], s["end_seq"]) for s in seg["use"]) == [(x, 3, 3), (y, 3, 3)]
    one = ok(
        api.get(f"/api/generate/segments/sns?pi={pi}&bill_no={b}&seq_pi={pi}&start=3&end=3&seq_prefix={y}", w.admin)
    )
    assert one["items"] == [f"{y}0003"]


def test_dual_sn_second_rule_per_bill(w, api, sql):
    """第二 SN 与第一 SN 一样按单据选规则：各单据的第二前缀可以不同，第二 SN 的流水按各自前缀排。"""
    org, pi, a = mk(w, ("M1", 2))
    _, _, b = mk(w, ("M1", 1), pi=pi, org=org)
    ok(api.post("/api/generate/dual", m(pi_no=pi, dual=True), w.admin))
    x, y, s1, s2 = P(), P(), P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    two = m(pi_no=pi, lines=[m(bill_no=a, seq=1)], custom=C(s1), slot=2)
    assert ok(api.post("/api/rules/line-select", two, w.admin))["layer"] == "PI"
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    two_b = m(pi_no=pi, lines=[m(bill_no=b, seq=1)], custom=C(s2), slot=2)
    assert ok(api.post("/api/rules/line-select", two_b, w.admin))["layer"] == "BILL"
    assert bill_reg(sql, b, "sn_bill_rule2")["prefix"] == s2
    # 第二前缀不能与本 PI 任何一个第一前缀互为开头
    bad = m(pi_no=pi, lines=[m(bill_no=b, seq=1)], custom=C(x + "9"), slot=2)
    assert api.post("/api/rules/line-select", bad, w.admin).code in ("RULE2_SAME_PREFIX", "RULE_PREFIX_OVERLAP")
    w.generate(pi, bills=[a])
    w.generate(pi, bills=[b])
    got = sql.all(
        "SELECT i.sn, s.sn2 FROM sn_item i JOIN sn_item_sub s ON s.item_id = i.id WHERE i.pi_no = %s ORDER BY i.sn", pi
    )
    assert [(r["sn"], r["sn2"]) for r in got] == sorted(
        [(f"{x}0001", f"{s1}0001"), (f"{x}0002", f"{s1}0002"), (f"{y}0001", f"{s2}0001")]
    )
    # 关闭双 SN 须先删号；删光后关闭，第二 SN 的单据规则一起清
    ok(w.delete(pi, bills=[a, b], reason="t"))
    ok(api.post("/api/generate/dual", m(pi_no=pi, dual=False), w.admin))
    assert bill_reg(sql, b, "sn_bill_rule2") is None and pi_reg(sql, pi, "sn_pi_rule2") is None


def test_bill_moved_to_other_pi_without_sns_follows_new_default(w, api, sql):
    """单据在金蝶改了 PI：没号的单据规则不再算（沿用新 PI 的默认规则）；有号的保留（前缀锁定）。"""
    org, pi, a = mk(w, ("M1", 1))
    _, _, b = mk(w, ("M1", 1), pi=pi, org=org)
    _, pi2, c = mk(w, ("M1", 1), org=org)
    x, y, z = P(), P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    ok(w.select(pi, [(b, 1)], custom=C(y)))
    ok(w.select(pi2, [(c, 1)], custom=C(z)))
    w.touch(b, pi=pi2)
    ok(w.api.post("/api/orders/sync", m(bill_no=b), w.admin))
    v = view(api, w, pi2, b)
    assert (v["layer"], v["prefix"]) == ("PI", z)
    w.generate(pi2, bills=[b])
    assert sns(sql, b) == [f"{z}0001"]


def test_bill_moved_to_other_pi_with_sns_keeps_prefix(w, api, sql):
    """单据有号后在金蝶改了 PI：前缀锁定，沿用新 PI 的默认规则（别的前缀）时不能生成（RULE_BILL_LOCKED），
    选回原前缀的规则后接着这个前缀的最大号出号。"""
    org, pi, a = mk(w, ("M1", 1), ("M1", 2))
    _, pi2, c = mk(w, ("M1", 1), org=org)
    x, z = P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(x)))
    w.generate(pi, lines=[(a, 1)])
    ok(w.select(pi2, [(c, 1)], custom=C(z)))
    w.touch(a, pi=pi2)
    w.compare()
    alert = next(
        x_ for x_ in ok(api.get(f"/api/orders/alerts?bill_no={a}", w.admin))["items"] if x_["alert_type"] == "PI"
    )
    ok(api.post(f"/api/orders/alerts/{alert['id']}/handle", m(action="CONFIRM"), w.admin))
    p = ok(w.preview(pi2, lines=[(a, 2)]))
    assert "RULE_BILL_LOCKED" in p["issues"][0]["issues"], str(p)
    assert w.select(pi2, [(a, 2)], custom=C(z)).code == "RULE_BILL_PREFIX_LOCKED"
    ok(w.select(pi2, [(a, 2)], custom=C(x)))
    w.generate(pi2, lines=[(a, 2)])
    assert sns(sql, a) == [f"{x}0001", f"{x}0002", f"{x}0003"]
