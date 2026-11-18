"""识别码（中间件）：SN = 前缀 + 识别码 + 流水 + …。前缀防重只看前缀层；前缀 + 自增值为一张 PI 独占的号段，
流水从 1 开始。同一客户编码下的 PI 依次取已用最大值 + 1（选规则只预显示，生成成功才占用），超出范围须改前缀；
前缀锁定第一次生成的 PI 的客户；号全删掉、改选后值仍归本 PI；同一客户的新 PI 默认带出最近一次用的自增规则。"""

from __future__ import annotations

from conftest import MES, m, ok, uid


def mk(w, *lines, pi=None, org=None, bill=None, customer="C1"):
    org = org or w.org(uid("O")[:10])
    pi = pi or uid("PI")
    bill = bill or uid("MO")
    w.order(bill, org, customer, pi, *lines)
    return org, pi, bill


def H() -> str:
    """前缀层文本（各用例唯一，不与别的用例的前缀一个是另一个的开头）。"""
    return "K" + uid("").replace("-", "") + "Q"


def C() -> str:
    return uid("CU")[:16]


def inc(head: str, start: int = 0, end: int = 3, tail: str = "", seq_len: int = 4, **kv) -> dict:
    return m(
        prefix=head, base=10, seq_len=seq_len, with_length=False, inc_start=start, inc_end=end, inc_tail=tail, **kv
    )


def sns(sql, pi: str) -> list[str]:
    return [r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE seq_pi_no = %s ORDER BY seq_dec", pi)]


def ctx(api, w, pi: str) -> dict:
    return ok(api.get(f"/api/generate/context?pi={pi}", w.admin)).body


def test_inc_values_per_customer_until_exhausted_then_change_prefix(w, api, sql):
    """同一客户的 PI 依次取 0、1、2、3，各自的号段流水从 1 开始；第 5 张选规则提示已用完；改前缀后从 0 开始。
    一个前缀一条规则：第一张 PI 生成时存入规则库（当前版本即它的配置），之后各 PI 的版本另出在它下面。"""
    org = w.org(uid("O")[:10])
    cust = C()
    head = H()
    pis = []
    for n in range(4):
        _, pi, bill = mk(w, ("M1", 2 + n), org=org, customer=cust)
        r = ok(w.select(pi, [(bill, 1)], custom=inc(head)))["rule"]
        assert r["inc"]["value"] == n and r["inc"]["prefix"] == f"{head}{n}", "选规则只预显示本 PI 将取的值"
        w.generate(pi)
        assert sns(sql, pi) == [f"{head}{n}{i:04d}" for i in range(1, 3 + n)], "每个号段流水从 1 开始"
        pis.append(pi)
    rules = sql.all("SELECT DISTINCT rule_id FROM sn_rule_version WHERE inc_head = %s", head)
    assert len(rules) == 1, "一个前缀一条规则"
    rid = rules[0]["rule_id"]
    v1 = sql.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = 1", rid)
    assert (v1["claimed_pi"], v1["inc_value"], v1["inc_customer"]) == (pis[0], 0, cust)
    assert sql.scalar("SELECT current_version FROM sn_rule WHERE id = %s", rid) == 1, "当前版本为第一张 PI 的配置"
    # 第 5 张：已用完，须改前缀
    _, p5, b5 = mk(w, ("M1", 2), org=org, customer=cust)
    r = w.select(p5, [(b5, 1)], custom=inc(head))
    assert r.code == "RULE_INC_EXHAUSTED", str(r)
    lib = w.select(p5, [(b5, 1)], rule_id=rid)
    assert lib.code == "RULE_INC_EXHAUSTED", "规则库里选同样提示已用完"
    opt = next(
        x for x in ok(api.get(f"/api/rules/options?pi={p5}&customer={cust}", w.admin)).body if x["rule_id"] == rid
    )
    assert opt["recommended"] and opt["inc_error"]["code"] == "RULE_INC_EXHAUSTED"
    # 默认规则：带出这个客户的自增规则，提示已用完
    rp = ctx(api, w, p5)["rule_prefix"]
    assert (rp["prefix"], rp["registered"], rp["custom"]["inc_end"]) == (head, False, 3)
    assert rp["inc_error"]["code"] == "RULE_INC_EXHAUSTED"
    # 改前缀：从起始值开始
    head2 = H()
    r = ok(w.select(p5, [(b5, 1)], custom=inc(head2)))["rule"]
    assert r["inc"]["prefix"] == f"{head2}0"
    w.generate(p5)
    assert sns(sql, p5) == [f"{head2}0{i:04d}" for i in (1, 2)]
    # 之后同一客户的新 PI 默认带出最近一次用的（新前缀，下一个值 1）
    _, p6, _b6 = mk(w, ("M1", 1), org=org, customer=cust)
    rp = ctx(api, w, p6)["rule_prefix"]
    assert (rp["prefix"], rp["custom"]["inc_value"], rp["inc_error"]) == (head2, 1, None)


def test_inc_preview_only_until_generated(w, sql):
    """选规则只预显示、不占用：两张 PI 都预显示 0，先生成的拿走 0；后一张的预演失效（须重新选规则），重选后为 1。"""
    org = w.org(uid("O")[:10])
    cust = C()
    head = H()
    _, pa, ba = mk(w, ("M1", 2), org=org, customer=cust)
    _, pb, bb = mk(w, ("M1", 2), org=org, customer=cust)
    assert ok(w.select(pa, [(ba, 1)], custom=inc(head)))["rule"]["inc"]["value"] == 0
    assert ok(w.select(pb, [(bb, 1)], custom=inc(head)))["rule"]["inc"]["value"] == 0
    assert sql.count("SELECT COUNT(*) FROM sn_rule_version WHERE inc_head = %s", head) == 0, "没生成不占用"
    w.generate(pb)
    p = ok(w.preview(pa))
    assert not p["generatable"], "预显示的值已被先生成的 PI 占走"
    assert "RULE_PI_PREFIX" in p["issues"][0]["issues"]
    assert ok(w.select(pa, [(ba, 1)], custom=inc(head)))["rule"]["inc"]["value"] == 1
    w.generate(pa)
    assert sns(sql, pa)[0] == f"{head}10001"
    assert sns(sql, pb)[0] == f"{head}00001"


def test_inc_value_kept_after_delete_and_reselect(w, sql):
    """号全删掉：值仍归本 PI，重新生成从 1 开始；别的 PI 拿不到它；改选别的规则再选回来仍是它。"""
    org = w.org(uid("O")[:10])
    cust = C()
    head = H()
    _, p1, b1 = mk(w, ("M1", 3), org=org, customer=cust)
    _, p2, b2 = mk(w, ("M1", 3), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(head)))
    w.generate(p1)
    ok(w.select(p2, [(b2, 1)], custom=inc(head)))
    w.generate(p2)
    assert sns(sql, p2) == [f"{head}1{i:04d}" for i in (1, 2, 3)]
    ok(w.delete(p2, bills=[b2]))
    assert sns(sql, p2) == []
    # 别的 PI 拿不到 1
    _, p3, b3 = mk(w, ("M1", 1), org=org, customer=cust)
    assert ok(w.select(p3, [(b3, 1)], custom=inc(head)))["rule"]["inc"]["value"] == 2
    # 本 PI 改选普通规则，再选回自增规则：仍是 1
    plain = "P" + uid("").replace("-", "")
    ok(w.select(p2, [(b2, 1)], custom=m(prefix=plain, base=10, seq_len=4, with_length=False)))
    assert ok(w.select(p2, [(b2, 1)], custom=inc(head)))["rule"]["inc"]["value"] == 1
    w.generate(p2)
    assert sns(sql, p2) == [f"{head}1{i:04d}" for i in (1, 2, 3)], "流水从 1 重排"
    assert sql.count("SELECT COUNT(*) FROM sn_rule_version WHERE inc_head = %s AND claimed_pi = %s", head, p2) == 1


def test_inc_customer_lock_width_lock_and_padding(w, api, sql):
    """前缀锁定第一次生成的 PI 的客户（别的客户须改前缀，规则库里选也一样）；自增位数锁定；范围超过一位自动补 0。"""
    org = w.org(uid("O")[:10])
    head = H()
    _, p1, b1 = mk(w, ("M1", 2), org=org, customer=C())
    ok(w.select(p1, [(b1, 1)], custom=inc(head, 0, 99)))
    w.generate(p1)
    assert sns(sql, p1) == [f"{head}00{i:04d}" for i in (1, 2)], "范围 0~99：补 0 到 2 位"
    rid = sql.scalar("SELECT rule_id FROM sn_rule_version WHERE inc_head = %s", head)
    _, p2, b2 = mk(w, ("M1", 2), org=org, customer=C())
    assert w.select(p2, [(b2, 1)], custom=inc(head, 0, 99)).code == "RULE_INC_CUSTOMER"
    assert w.select(p2, [(b2, 1)], rule_id=rid).code == "RULE_INC_CUSTOMER"
    _, p3, b3 = mk(
        w, ("M1", 2), org=org, customer=sql.scalar("SELECT inc_customer FROM sn_rule_version WHERE rule_id = %s", rid)
    )
    assert w.select(p3, [(b3, 1)], custom=inc(head, 0, 9)).code == "RULE_INC_WIDTH"
    assert w.select(p3, [(b3, 1)], custom=inc(head, 0, 999)).code == "RULE_INC_WIDTH"
    # 同位数内可改范围；从规则库选沿用第一张 PI 的配置
    r = ok(w.select(p3, [(b3, 1)], rule_id=rid))["rule"]
    assert (r["plan"], r["inc"]["prefix"]) == ("INC", f"{head}01")
    w.generate(p3)
    assert sns(sql, p3)[0] == f"{head}010001"
    assert w.select(p3, [(b3, 1)], rule_id=rid, new_prefix="ZZ").code == "RULE_INC_PREFIX_CUSTOM"
    # 范围非法
    _, p4, b4 = mk(w, ("M1", 1), org=org)
    assert w.select(p4, [(b4, 1)], custom=inc(H(), 5, 3)).code == "RULE_INC_RANGE_INVALID"
    assert w.select(p4, [(b4, 1)], custom=inc(H(), 0, 1000)).code == "RULE_INC_RANGE_INVALID"
    # 示例号：按本 PI 将取的值
    pv = ok(api.post("/api/rules/preview", inc(head, 0, 99, pi_no=p1), w.admin))
    assert pv["inc"]["prefix"] == f"{head}00" and pv["samples"][0]["sn"] == f"{head}000001"


def test_inc_prefix_dedup_by_head_only(w, api, sql):
    """前缀防重只看前缀层：普通前缀与它相同或一个是另一个的开头都不行（含 ABC1 这类号段前缀）；
    带自增的前缀层与普通前缀、别的自增前缀一个是另一个的开头也不行。"""
    org = w.org(uid("O")[:10])
    cust = C()
    head = H()
    _, p1, b1 = mk(w, ("M1", 2), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(head, tail="-")))
    w.generate(p1)
    assert sns(sql, p1)[0] == f"{head}0-0001", "识别码与流水之间可放固定字符"
    for pre in (head, head + "1", head + "0-", head[:-1]):
        body = m(rule_code=uid("R"), rule_name="x", prefix=pre, base=10, seq_len=4)
        assert api.post("/api/rules", body, w.admin).code == "RULE_PREFIX_OVERLAP", pre
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    assert w.select(p2, [(b2, 1)], custom=m(prefix=head + "7", base=10, seq_len=4, with_length=False)).code == (
        "RULE_PREFIX_OVERLAP"
    )
    assert w.select(p2, [(b2, 1)], custom=inc(head + "X")).code == "RULE_PREFIX_OVERLAP"
    plain = "P" + uid("").replace("-", "")
    w.rule(plain, seq_len=4, with_length=False)
    assert w.select(p2, [(b2, 1)], custom=inc(plain[:-1])).code == "RULE_PREFIX_OVERLAP"


def test_inc_rule_library_readonly_and_mes_fields(w, api, sql):
    """规则库里带识别码的规则只能改名称；规则库按前缀层归档，列出各 PI 的号段；推给 MES 的报文字段不变
    （serial 为纯流水，sn 为完整 SN）。"""
    org = w.org(uid("O")[:10])
    w.enable_mes(org, batch_size=50)
    cust = C()
    head = H()
    _, p1, b1 = mk(w, ("M1", 2), org=org, customer=cust)
    _, p2, b2 = mk(w, ("M1", 2), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(head)))
    w.generate(p1)
    ok(w.select(p2, [(b2, 1)], custom=inc(head)))
    w.generate(p2)
    rid = sql.scalar("SELECT rule_id FROM sn_rule_version WHERE inc_head = %s LIMIT 1", head)
    rule = sql.one("SELECT * FROM sn_rule WHERE id = %s", rid)
    body = m(rule_name="改名", prefix=f"{head}0", base=10, seq_len=5, with_length=False)
    assert api.put(f"/api/rules/{rid}", body, w.admin).code == "RULE_INC_READONLY"
    cur = sql.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = 1", rid)
    same = m(rule_name="改名", prefix=cur["prefix"], base=10, seq_len=4, with_length=False, suffix="")
    assert ok(api.put(f"/api/rules/{rid}", same, w.admin))["action"] == "RENAMED"
    assert rule["rule_code"].startswith("PI-")
    g = next(x for x in ok(api.get(f"/api/rules/prefixes?q={head}", w.admin)).body if x["prefix"] == head)
    assert g["inc"]["customer"] == cust
    assert [(v["text"], v["pi_no"]) for v in g["inc"]["values"]] == [("0", p1), ("1", p2)]
    u = ok(api.get(f"/api/rules/prefixes/usage?prefix={head}", w.admin))
    assert {(x["pi_no"], x["seq_prefix"], x["qty"]) for x in u["pis"]} == {(p1, f"{head}0", 2), (p2, f"{head}1", 2)}
    w.allocate(p2)
    w.push()
    items = [i for x in MES.received if x["batch"].get("pi") == p2 for i in x["batch"]["items"]]
    first = min(items, key=lambda i: int(i["serialDec"]))
    assert (first["sn"], first["serial"], first["serialDec"], first["serialPi"]) == (f"{head}10001", "0001", "1", p2)


def test_inc_taken_between_preview_and_run(w, api, sql):
    """预演后、确认生成前，别的 PI 先生成占走了预显示的值：确认生成报 RULE_INC_TAKEN（预演已失效，
    请重新选规则），不写号。"""
    org = w.org(uid("O")[:10])
    cust = C()
    head = H()
    _, pa, ba = mk(w, ("M1", 2), org=org, customer=cust)
    _, pb, bb = mk(w, ("M1", 2), org=org, customer=cust)
    ok(w.select(pa, [(ba, 1)], custom=inc(head)))
    ok(w.select(pb, [(bb, 1)], custom=inc(head)))
    p = ok(w.preview(pa))
    assert p["generatable"]
    w.generate(pb)
    r = api.post("/api/generate/run", m(token=p["token"]), w.admin)
    assert r.code == "RULE_INC_TAKEN", str(r)
    assert r.body["params"]["pi"] == pb
    assert sns(sql, pa) == []


def test_inc_registered_library_rule_used_up_before_generation(w, api, sql):
    """PI 从规则库选了自增规则（只预显示），生成前别的 PI 把最后一个值用了：页面照常打开，提示已用完；生成被拦。"""
    org = w.org(uid("O")[:10])
    cust = C()
    head = H()
    _, p1, b1 = mk(w, ("M1", 1), org=org, customer=cust)
    ok(w.select(p1, [(b1, 1)], custom=inc(head, 0, 1)))
    w.generate(p1)
    rid = sql.scalar("SELECT rule_id FROM sn_rule_version WHERE inc_head = %s LIMIT 1", head)
    _, p2, b2 = mk(w, ("M1", 1), org=org, customer=cust)
    _, p3, b3 = mk(w, ("M1", 1), org=org, customer=cust)
    assert ok(w.select(p2, [(b2, 1)], rule_id=rid))["rule"]["inc"]["prefix"] == f"{head}1"
    ok(w.select(p3, [(b3, 1)], custom=inc(head, 0, 1)))
    w.generate(p3)
    rp = ctx(api, w, p2)["rule_prefix"]
    assert (rp["prefix"], rp["registered"], rp["inc_error"]["code"]) == (head, True, "RULE_INC_EXHAUSTED")
    p = ok(w.preview(p2))
    assert not p["generatable"]
    r = api.post("/api/generate/run", m(token=p.body.get("token") or "x"), w.admin)
    assert r.status != 200
