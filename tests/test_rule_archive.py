"""规则库按前缀归档、一个 PI 一个规则（2026-11-17 变更）：
- 规则库按前缀归档（GET /rules/prefixes、/rules/prefixes/usage），新建规则不填 PI；
- 选规则作用于整张 PI 的全部有效物料行；PI 已生成的号还在时前缀锁定，号全部删掉后可改选；
- 自定义规则与规则库里前缀、格式完全相同的规则归到那条规则下，不另建；
- 别的 PI 用同一前缀时流水接号（共用前缀，原逻辑不变）；MES 报文不变。"""

from __future__ import annotations

from conftest import ok, uid


def mk(w, *lines, pi=None, org=None, bill=None):
    org = org or w.org(uid("O")[:10])
    pi = pi or uid("PI")
    bill = bill or uid("MO")
    w.order(bill, org, "C1", pi, *lines)
    return org, pi, bill


def P() -> str:
    return "A" + uid("").replace("-", "")


def group(api, w, prefix: str) -> dict:
    return next(g for g in ok(api.get(f"/api/rules/prefixes?q={prefix}", w.admin)).body if g["prefix"] == prefix)


def line_rules(sql, pi: str) -> dict:
    return {(r["bill_no"], r["seq"]): r for r in sql.all("SELECT * FROM sn_line_rule WHERE pi_no = %s", pi)}


def test_create_rule_without_pi(w, api, sql):
    """新建规则不填 PI：未分类；填了照旧记为 PI 分类（只用于推荐）。"""
    pre = P()
    r = ok(
        api.post(
            "/api/rules",
            {"rule_code": uid("R"), "rule_name": "n", "prefix": pre, "base": 10, "seq_len": 5},
            w.admin,
        )
    )["rule"]
    assert (r["category_scope"], r["category_value"]) == ("NONE", "")
    g = group(api, w, pre)
    assert (g["base"], g["seq_len"], g["used"], g["pis"], g["selected_pis"]) == (10, 5, False, [], [])
    assert [x["rule"]["rule_code"] for x in g["rules"]] == [r["rule_code"]]


def test_select_applies_to_whole_pi_and_prefix_locks(w, api, sql):
    """选规则作用于整张 PI：点一个物料行，本 PI 别的单据的物料行也改记为同一规则；生成后前缀锁定，
    换别的前缀报 RULE_PI_PREFIX_LOCKED，同前缀可以（如改后缀）；号全部删掉后可以改选。"""
    org, pi, b1 = mk(w, ("M1", 3), ("M2", 2))
    _, _, b2 = mk(w, ("M1", 4), pi=pi, org=org)
    pre, other = P(), P()
    r = ok(w.select(pi, [(b1, 1)], custom={"prefix": pre, "base": 10, "seq_len": 4, "with_length": False}))
    assert r["lines"] == 3
    sels = line_rules(sql, pi)
    assert set(sels) == {(b1, 1), (b1, 2), (b2, 1)}
    assert all(x["rule_id"] is None and pre in x["custom_json"] for x in sels.values())
    # 还没有号：可以改选，仍作用于整张 PI
    rid = w.rule(other, seq_len=5, with_length=False)
    ok(w.select(pi, [(b2, 1)], rule_id=rid))
    assert {x["rule_id"] for x in line_rules(sql, pi).values()} == {rid}
    ok(w.select(pi, [(b1, 1)], custom={"prefix": pre, "base": 10, "seq_len": 4, "with_length": False}))
    w.generate(pi)
    # 已有号：前缀锁定
    res = w.select(pi, [(b1, 1)], rule_id=rid)
    assert res.code == "RULE_PI_PREFIX_LOCKED", str(res)
    assert w.bind_pi(pi, rid).code == "RULE_PI_PREFIX_LOCKED"
    assert {x["rule_id"] for x in line_rules(sql, pi).values()} == {
        sql.scalar("SELECT id FROM sn_rule WHERE rule_code = %s", f"PI-{pi}-1")
    }, "没有改动"
    # 同前缀（改后缀）可以：另成一条规则，同一前缀
    c = {"prefix": pre, "base": 10, "seq_len": 4, "with_length": False}
    ok(w.select(pi, [(b1, 1)], custom={**c, "suffix": "Z"}))
    # 号全部删掉：回到可以改选
    ok(w.delete(pi, bills=[b1, b2], reason="t"))
    ok(w.select(pi, [(b1, 1)], rule_id=rid))
    assert {x["rule_id"] for x in line_rules(sql, pi).values()} == {rid}


def test_generation_blocked_when_rule_prefix_differs_from_pi(w, api, sql):
    """本 PI 已用前缀生成过号后，规则在规则库换了新前缀（另出未归属的版本）：选这条规则报前缀锁定；
    换前缀之前就选好的物料行预演标「与本 PI 已用前缀不同」，不能生成。"""
    org, pi, b1 = mk(w, ("M1", 2))
    pre = P()
    rid = w.rule(pre, seq_len=5, with_length=False)
    ok(w.select(pi, [(b1, 1)], rule_id=rid))
    w.generate(pi)
    body = {"rule_name": "x", "prefix": P(), "base": 10, "seq_len": 5, "with_length": False}
    assert ok(api.put(f"/api/rules/{rid}", body, w.admin))["action"] == "NEW_VERSION"
    _, _, b2 = mk(w, ("M1", 3), pi=pi, org=org)
    # 新单据的物料行还没选规则：选这条规则（当前版本已换前缀）报前缀锁定
    assert w.select(pi, [(b2, 1)], rule_id=rid).code == "RULE_PI_PREFIX_LOCKED"
    # 换前缀之前就选好了这条规则的物料行
    sql.exec(
        "INSERT INTO sn_line_rule(bill_no, seq, pi_no, rule_id, custom_json, set_by, set_at) "
        "VALUES (%s, 1, %s, %s, NULL, 'admin', NOW())",
        b2,
        pi,
        rid,
    )
    p = ok(w.preview(pi, bills=[b2]))
    assert not p["generatable"]
    assert "RULE_PI_PREFIX" in p["issues"][0]["issues"], str(p)


def test_custom_rule_reuses_same_format_rule_and_continues_serials(w, api, sql):
    """自定义规则与规则库里前缀、格式完全相同的规则：归到那条规则下（不另建 PI-PI号-n），别的 PI 已用时共用前缀，
    流水接在共同的最大号之后；上限自动突破开关照旧记在物料行上。"""
    org = w.org(uid("O")[:10])
    _, p1, b1 = mk(w, ("M1", 5), org=org)
    _, p2, b2 = mk(w, ("M1", 3), org=org)
    pre = P()
    c = {"prefix": pre, "base": 10, "seq_len": 4, "with_length": False}
    ok(w.select(p1, [(b1, 1)], custom=c))
    w.generate(p1)
    rid = sql.scalar("SELECT id FROM sn_rule WHERE rule_code = %s", f"PI-{p1}-1")
    r = ok(w.select(p2, [(b2, 1)], custom={**c, "seq_grow": True}))["rule"]
    assert (r["rule_id"], r["plan"], r["seq_grow"]) == (rid, "SHARE", True)
    sel = line_rules(sql, p2)[(b2, 1)]
    assert sel["rule_id"] == rid and '"seq_grow": true' in sel["custom_json"]
    w.generate(p2)
    assert [x["sn"] for x in sql.all("SELECT sn FROM sn_item WHERE pi_no = %s ORDER BY seq_dec", p2)] == [
        f"{pre}{i:04d}" for i in (6, 7, 8)
    ]
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE rule_code LIKE %s", f"PI-{p2}-%") == 0, "不另建规则"
    assert sql.scalar("SELECT custom_json FROM sn_line_rule WHERE bill_no = %s", b2) == '{"seq_grow": true}'
    # 规则库：一个前缀一组，一条规则，两张 PI 都在
    g = group(api, w, pre)
    assert [x["rule"]["id"] for x in g["rules"]] == [rid]
    assert g["pis"] == sorted([p1, p2]) and g["rules"][0]["pis"] == sorted([p1, p2])
    assert (g["top_seq"], g["top_text"]) == (8, "0008")
    u = ok(api.get(f"/api/rules/prefixes/usage?prefix={pre}", w.admin)).body
    assert [(x["pi_no"], x["min_text"], x["max_text"], x["qty"]) for x in u["pis"]] == [
        (p1, "0001", "0005", 5),
        (p2, "0006", "0008", 3),
    ]


def test_custom_rule_selected_before_other_pi_generates_is_merged_at_generation(w, api, sql):
    """两张 PI 先后选了同一自定义格式，一张先生成（存入规则库）后，另一张生成时归到那条规则下，不另建重复规则。"""
    org = w.org(uid("O")[:10])
    _, p1, b1 = mk(w, ("M1", 2), org=org)
    _, p2, b2 = mk(w, ("M1", 2), org=org)
    pre = P()
    c = {"prefix": pre, "base": 10, "seq_len": 4, "with_length": False}
    ok(w.select(p1, [(b1, 1)], custom=c))
    ok(w.select(p2, [(b2, 1)], custom=c))
    w.generate(p1)
    w.generate(p2)
    rid = sql.scalar("SELECT id FROM sn_rule WHERE rule_code = %s", f"PI-{p1}-1")
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE rule_code LIKE %s", f"PI-{p2}-%") == 0
    assert line_rules(sql, p2)[(b2, 1)]["rule_id"] == rid
    got = [x["seq_dec"] for x in sql.all("SELECT seq_dec FROM sn_item WHERE pi_no = %s ORDER BY seq_dec", p2)]
    assert got == [3, 4]
    assert group(api, w, pre)["pis"] == sorted([p1, p2])


def test_changing_rule_of_multi_org_pi_needs_all_orgs(w, api, sql):
    """PI 跨两个组织：只授权 A 的普通操作员可以给还没有规则的 PI 选规则（作用于整张 PI），
    B 的物料行已选了别的规则时改选须对全部组织有授权；沿用同一规则可以。"""
    a, b = w.org(uid("O")[:10]), w.org(uid("O")[:10])
    _, pi, ba = mk(w, ("M1", 2), org=a)
    _, _, bb = mk(w, ("M1", 2), pi=pi, org=b)
    tok = w.user(uid("E")[:16], "operator", a)
    r1, r2 = w.rule(P(), seq_len=5, with_length=False), w.rule(P(), seq_len=5, with_length=False)
    body = {"pi_no": pi, "lines": [{"bill_no": ba, "seq": 1}], "rule_id": r1}
    assert ok(api.post("/api/rules/line-select", body, tok))["lines"] == 2
    assert {x["rule_id"] for x in line_rules(sql, pi).values()} == {r1}
    ok(api.post("/api/rules/line-select", body, tok))
    assert api.post("/api/rules/line-select", {**body, "rule_id": r2}, tok).code == "FACTORY_FORBIDDEN"
    ok(w.select(pi, [(ba, 1)], rule_id=r2))
    assert {x["rule_id"] for x in line_rules(sql, pi).values()} == {r2}
