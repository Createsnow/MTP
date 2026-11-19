"""识别码适用客户（2026-11-19）：识别码组件可手动指定一组客户（只能选已同步的订单客户），这组客户下的 PI 共用一套
识别码递增序号。生成页里改的本 PI 生成成功才生效（本 PI 待加入的客户算进本 PI 取值）；规则库页面改的保存即生效。
删除客户：号段上还有号的 PI 不动（可继续生成）；号已全删掉的号段释放、还没生成的 PI 解除登记，之后都不能再用这个前缀；
已释放的号段可给别的 PI 改选（流水接着原最大号）；客户加回来时取新的最大值 + 1。"""

from __future__ import annotations

from conftest import m, ok, uid
from test_inc_prefix import H, inc, sns


def C() -> str:
    return uid("CU")[:16]


def mk(w, org, customer, qty=2):
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, customer, pi, ("M1", qty))
    return pi, bill


def rule_of(sql, head: str) -> dict:
    return sql.one("SELECT * FROM sn_rule WHERE prefix = %s", head)


def custs(sql, head: str) -> list[str]:
    return [c for c in (rule_of(sql, head)["inc_customers"] or "").split(",") if c]


def test_customers_share_one_sequence(w, api, sql):
    """A 的 PI 新建前缀、指定客户 A、B：A 取 0、B 取 1、A 再取 2；C 不在清单里不能用；B 的新 PI 默认带出这个前缀。"""
    org = w.org(uid("O")[:10])
    a, b, c = C(), C(), C()
    head = H()
    pa, ba = mk(w, org, a)
    pb, bb = mk(w, org, b)
    pc, bc = mk(w, org, c)
    r = ok(w.select(pa, [(ba, 1)], custom=inc(head, 0, 9, inc_customers=[a, b])))["rule"]
    assert r["inc"]["value"] == 0
    w.generate(pa)
    assert custs(sql, head) == sorted([a, b])
    rid = rule_of(sql, head)["id"]
    rp = ok(api.get(f"/api/generate/context?pi={pb}", w.admin)).body["rule_prefix"]
    assert (rp["prefix"], rp["custom"]["inc_value"]) == (head, 1), "B 的新 PI 默认带出这组客户最近用的前缀"
    assert sorted(rp["custom"]["inc_customers"]) == sorted([a, b])
    ok(w.select(pb, [(bb, 1)], rule_id=rid))
    w.generate(pb)
    pa2, ba2 = mk(w, org, a)
    ok(w.select(pa2, [(ba2, 1)], custom=inc(head, 0, 9)))
    w.generate(pa2)
    assert [sns(sql, p)[0] for p in (pa, pb, pa2)] == [f"{head}00001", f"{head}10001", f"{head}20001"]
    assert w.select(pc, [(bc, 1)], rule_id=rid).code == "RULE_INC_CUSTOMER"
    e = w.select(pc, [(bc, 1)], custom=inc(head, 0, 9))
    assert e.code == "RULE_INC_CUSTOMER" and a in e.body["params"]["customers"]
    opt = next(x for x in ok(api.get(f"/api/rules/options?pi={pb}&customer={b}", w.admin)).body if x["rule_id"] == rid)
    assert opt["recommended"] and sorted(opt["inc_customers"]) == sorted([a, b])


def test_customer_list_validation(w, sql):
    """清单须含本 PI 的客户、只能选已同步的订单客户、最多 30 个。"""
    org = w.org(uid("O")[:10])
    a, b = C(), C()
    pa, ba = mk(w, org, a)
    mk(w, org, b)
    head = H()
    assert w.select(pa, [(ba, 1)], custom=inc(head, inc_customers=[b])).code == "RULE_INC_CUSTOMER_SELF"
    assert w.select(pa, [(ba, 1)], custom=inc(head, inc_customers=[a, "NOPE" + uid("")])).code == (
        "RULE_INC_CUSTOMER_UNKNOWN"
    )
    many = [a] + [f"X{i}" for i in range(30)]
    assert w.select(pa, [(ba, 1)], custom=inc(head, inc_customers=many)).code == "RULE_INC_CUSTOMERS_TOO_MANY"


def test_generate_page_edit_takes_effect_after_generation(w, sql):
    """前缀已有规则：A 的 PI 在识别码组件里加上 C，只登记不生效（C 的 PI 还不能用）；A 的 PI 生成成功后才生效。
    C 的 PI 也可以在自己的 PI 上把 C 加进来，一步完成。"""
    org = w.org(uid("O")[:10])
    a, c, d = C(), C(), C()
    head = H()
    pa, ba = mk(w, org, a)
    ok(w.select(pa, [(ba, 1)], custom=inc(head, 0, 9, inc_customers=[a])))
    w.generate(pa)
    pa2, ba2 = mk(w, org, a)
    pc, bc = mk(w, org, c)
    ok(w.select(pa2, [(ba2, 1)], custom=inc(head, 0, 9, inc_customers=[a, c])))
    assert custs(sql, head) == [a], "生成成功前不生效"
    assert w.select(pc, [(bc, 1)], custom=inc(head, 0, 9)).code == "RULE_INC_CUSTOMER"
    w.generate(pa2)
    assert custs(sql, head) == sorted([a, c])
    r = ok(w.select(pc, [(bc, 1)], custom=inc(head, 0, 9)))["rule"]
    assert r["inc"]["value"] == 2
    # D 在自己的 PI 上把自己加进来：预显示与生成都算进去
    pd, bd = mk(w, org, d)
    r = ok(w.select(pd, [(bd, 1)], custom=inc(head, 0, 9, inc_customers=[a, c, d])))["rule"]
    assert r["inc"]["value"] == 2
    w.generate(pd)
    assert custs(sql, head) == sorted([a, c, d])
    assert sns(sql, pd)[0] == f"{head}20001"


def test_library_remove_customer_keeps_releases_and_unbinds(w, api, sql):
    """规则库页面删除客户 B（保存即生效）：B 下号段上还有号的 PI 不动、可继续生成；号已全删掉的号段释放、
    不能再用这个前缀（生成页不再显示这个号段）；还没生成的 PI 解除登记。查看影响接口先列出这些 PI。"""
    org = w.org(uid("O")[:10])
    a, b = C(), C()
    head = H()
    pa, ba = mk(w, org, a)
    keep, bk = mk(w, org, b, qty=1)
    gone, bg = mk(w, org, b, qty=1)
    wait, bw = mk(w, org, b, qty=1)
    ok(w.select(pa, [(ba, 1)], custom=inc(head, 0, 9, inc_customers=[a, b])))
    w.generate(pa)
    rid = rule_of(sql, head)["id"]
    for p, bill in ((keep, bk), (gone, bg)):
        ok(w.select(p, [(bill, 1)], rule_id=rid))
        w.generate(p)
    ok(w.delete(gone, bills=[bg]))
    ok(w.select(wait, [(bw, 1)], rule_id=rid))
    imp = ok(api.post(f"/api/rules/{rid}/inc-customers/impact", m(customers=[a]), w.admin))
    acts = {x["pi_no"]: x["actions"] for x in imp["impact"]}
    assert acts == {keep: ["KEEP"], gone: ["RELEASE", "UNBIND"], wait: ["UNBIND"]}, acts
    ok(api.put(f"/api/rules/{rid}/inc-customers", m(customers=[a]), w.admin))
    assert custs(sql, head) == [a]
    assert sql.scalar("SELECT MAX(inc_released) FROM sn_rule_version WHERE claimed_pi = %s", gone) == 1
    assert sql.count("SELECT COUNT(*) FROM sn_pi_rule WHERE pi_no IN (%s, %s)", gone, wait) == 0
    # 还有号的继续在自己的号段上生成
    w.order(bk, org, b, keep, ("M1", 2))
    w.generate(keep)
    assert sns(sql, keep) == [f"{head}10001", f"{head}10002"]
    # 号已全删掉的、还没生成的都不能再用这个前缀；生成页不再显示原号段
    assert w.select(gone, [(bg, 1)], rule_id=rid).code == "RULE_INC_CUSTOMER"
    assert w.select(wait, [(bw, 1)], rule_id=rid).code == "RULE_INC_CUSTOMER"
    locked = ok(api.get(f"/api/generate/context?pi={gone}", w.admin)).body.get("prefix_locked") or {}
    assert f"{head}2" not in locked and head not in locked
    # 规则库页面：号段状态
    g = next(x for x in ok(api.get(f"/api/rules/prefixes?q={head}", w.admin)).body if x["prefix"] == head)
    st = {v["pi_no"]: v["status"] for v in g["inc"]["values"]}
    assert st == {pa: "USED", keep: "REMOVED", gone: "RELEASED"}, st


def test_released_segment_can_be_picked_and_readd_takes_new_value(w, api, sql):
    """已释放的号段可给清单里的客户的 PI 改选（流水接着原最大号），默认仍取已用最大值 + 1；
    客户加回来后，它之前号全删掉的 PI 取新的最大值 + 1，不拿回原号段。"""
    org = w.org(uid("O")[:10])
    a, b = C(), C()
    head = H()
    pa, ba = mk(w, org, a)
    pb, bb = mk(w, org, b, qty=3)
    ok(w.select(pa, [(ba, 1)], custom=inc(head, 0, 9, inc_customers=[a, b])))
    w.generate(pa)
    rid = rule_of(sql, head)["id"]
    ok(w.select(pb, [(bb, 1)], rule_id=rid))
    w.generate(pb)
    ok(w.delete(pb, bills=[bb]))
    ok(api.put(f"/api/rules/{rid}/inc-customers", m(customers=[a]), w.admin))
    # A 的新 PI：默认取 2（已释放的 1 也计入已用最大值），可以改选 1
    pa2, ba2 = mk(w, org, a)
    pv = ok(api.post("/api/rules/preview", inc(head, 0, 9, pi_no=pa2), w.admin))
    assert pv["inc"]["value"] == 2 and [f["value"] for f in pv["inc"]["free"]] == [1]
    assert w.select(pa2, [(ba2, 1)], custom=inc(head, 0, 9, seq_len=5, inc_pick=1)).code == "RULE_PREFIX_FORMAT_LOCKED"
    assert w.select(pa2, [(ba2, 1)], custom=inc(head, 0, 9, inc_pick=3)).code == "RULE_INC_PICK_INVALID"
    r = ok(w.select(pa2, [(ba2, 1)], custom=inc(head, 0, 9, inc_pick=1)))["rule"]
    assert r["inc"]["prefix"] == f"{head}1"
    w.generate(pa2)
    assert sns(sql, pa2) == [f"{head}10004", f"{head}10005"], "流水接着原号段的最大号"
    # B 加回来：B 原来号全删掉的 PI 取新的最大值 + 1
    ok(api.put(f"/api/rules/{rid}/inc-customers", m(customers=[a, b]), w.admin))
    r = ok(w.select(pb, [(bb, 1)], rule_id=rid))["rule"]
    assert r["inc"]["value"] == 2


def test_pending_edits_merge_with_library_changes(w, api, sql):
    """生成页的修改按「加了哪些、删了哪些」叠加到最新的清单上：A 的 PI 选规则时删了 C，之后规则库加了 D，
    A 的 PI 生成成功后清单为 A、D（D 不被覆盖掉）。"""
    org = w.org(uid("O")[:10])
    a, c, d = C(), C(), C()
    mk(w, org, c)
    mk(w, org, d)
    head = H()
    pa, ba = mk(w, org, a)
    ok(w.select(pa, [(ba, 1)], custom=inc(head, 0, 9, inc_customers=[a, c])))
    w.generate(pa)
    rid = rule_of(sql, head)["id"]
    pa2, ba2 = mk(w, org, a)
    ok(w.select(pa2, [(ba2, 1)], custom=inc(head, 0, 9, inc_customers=[a])))
    ok(api.put(f"/api/rules/{rid}/inc-customers", m(customers=[a, c, d]), w.admin))
    w.generate(pa2)
    assert custs(sql, head) == sorted([a, d])
