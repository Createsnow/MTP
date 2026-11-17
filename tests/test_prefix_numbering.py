"""以规则前缀为排号基准（2026-11-17 变更，取代按 PI 的号段预留）：
- 同一前缀下各 PI 共用一套十进制流水，接在前缀最大号之后；(seq_prefix, seq_dec) 唯一；
- 项次拦截：项次已开始（有现存的号）的不拦；还没开始的，同前缀登记的 PI 里还有更小项次（或项次为空 / 不是数字）的行
  有额度就拦（ATTACH_ORDER_BLOCKED，列出挡路的行）；删过号的行、失效的行不挡；一起生成的行彼此不挡；
- 排号顺序：点选物料行同项次按点击先后，勾整单同项次按单据勾选先后、单据内按行号；
- 一个 PI 一个规则前缀，登记在 PI 上，新单据继承；有号时锁定，没号时可改；
- 确认生成时同前缀别的 PI 先生成了：自动顺延；流水上限自动突破按前缀。MES 报文不变。"""

from __future__ import annotations

from conftest import m, ok, uid


def P() -> str:
    return "Q" + uid("").replace("-", "")


def C(prefix: str, **more) -> dict:
    return m(**{"prefix": prefix, "base": 10, "seq_len": 4, "with_length": False, **more})


def bill(w, pi: str, *lines, org: str | None = None) -> str:
    """建单据：lines = (物料, 数量, 附件项次)。"""
    no = uid("MO")
    w.order(no, org or w.org(uid("O")[:10]), "C1", pi, *[(mat, qty, "100", att) for mat, qty, att in lines])
    return no


def seqs(sql, no: str) -> dict[int, list[int]]:
    """单据各物料行的流水（升序）。"""
    out: dict[int, list[int]] = {}
    for r in sql.all("SELECT bill_seq, seq_dec FROM sn_item WHERE bill_no = %s ORDER BY seq_dec", no):
        out.setdefault(int(r["bill_seq"]), []).append(int(r["seq_dec"]))
    return out


def issues(p) -> dict[tuple[str, int], list[str]]:
    return {(x["bill_no"], x["seq"]): x["issues"] for x in p["issues"]}


def test_same_prefix_pis_share_one_serial(w, sql):
    """同一前缀下前一个 PI 生成后，后选这个前缀的 PI 从前缀最大号 + 1 接号；号上记前缀，两张 PI 的流水不重。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 3, "1"))
    b = bill(w, pb, ("M1", 2, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    w.generate(pa)
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    w.generate(pb)
    assert seqs(sql, a) == {1: [1, 2, 3]}
    assert seqs(sql, b) == {1: [4, 5]}
    assert {r["seq_prefix"] for r in sql.all("SELECT seq_prefix FROM sn_item WHERE bill_no IN (%s, %s)", a, b)} == {pre}
    assert sql.scalar("SELECT last_seq_dec FROM sn_prefix_counter WHERE prefix = %s", pre) == 5
    # 号段弹窗、空号段按前缀
    g = ok(w.api.get(f"/api/generate/gaps?pi={pb}", w.admin))
    assert (g["prefix"], g["last_seq"], g["next_seq"], g["reserved"]) == (pre, 5, 6, [])


def test_attach_order_blocks_across_pis_until_lower_items_done(w, sql):
    """项次 2 还没开始：同前缀 PI-B 的项次 1 没生成完时，PI-A 的项次 2 被拦（列出挡路的行）；
    同一次勾选项次 1、2 一起生成可以（项次 1 在前）；B 的项次 1 生成完后 A 的项次 2 可以生成。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 2, "1"), ("M2", 2, "2"))
    b = bill(w, pb, ("M1", 3, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    # A 只点选项次 2：被本 PI 的项次 1 和 B 的项次 1 挡
    p = ok(w.preview(pa, lines=[(a, 2)]))
    assert not p["generatable"]
    assert issues(p) == {(a, 2): ["ATTACH_ORDER_BLOCKED"]}
    got = {(x["pi_no"], x["bill_no"], x["seq"], x["reason"]) for x in p["issues"][0]["blockers"]}
    assert got == {(pa, a, 1, "NOT_GENERATED"), (pb, b, 1, "NOT_GENERATED")}
    # A 整单（项次 1、2 一起）：仍被 B 的项次 1 挡
    p = ok(w.preview(pa, bills=[a]))
    assert issues(p) == {(a, 2): ["ATTACH_ORDER_BLOCKED"]}
    assert [(x["pi_no"], x["bill_no"]) for x in p["issues"][0]["blockers"]] == [(pb, b)]
    # 生成页详情：物料行带拦截原因与挡路的行
    c = ok(w.api.get(f"/api/generate/context?pi={pa}", w.admin))
    line = next(x for x in c["bills"][0]["lines"] if x["seq"] == 2)
    assert line["issues"] == ["ATTACH_ORDER_BLOCKED"] and line["blockers"][0]["bill_no"] == b
    # 只生成 A 的项次 1，可以（项次 1 没有更小的项次）
    w.generate(pa, lines=[(a, 1)])
    assert not ok(w.preview(pa, lines=[(a, 2)]))["generatable"], "B 的项次 1 还没生成完"
    w.generate(pb)
    w.generate(pa, lines=[(a, 2)])
    assert seqs(sql, a) == {1: [1, 2], 2: [6, 7]}
    assert seqs(sql, b) == {1: [3, 4, 5]}


def test_started_item_not_blocked_by_late_lower_item(w, sql):
    """项次 2 已开始（有现存的号）：之后同步进来项次 1 的单据，项次 2 剩下的行照常生成、接在最大号之后，
    项次 1 也可以生成；还没开始的项次 3 要等项次 1 新增的部分生成完。"""
    pre = P()
    pi = uid("PI")
    org = w.org(uid("O")[:10])
    a = bill(w, pi, ("M1", 1, "1"), ("M2", 1, "2"), org=org)
    ok(w.select(pi, [(a, 1)], custom=C(pre)))
    w.generate(pi)
    late = bill(w, pi, ("M1", 2, "1"), ("M2", 2, "2"), ("M3", 2, "3"), org=org)
    p = ok(w.preview(pi, lines=[(late, 2)]))
    assert p["generatable"], "项次 2 已开始，项次 1 的新单据不挡"
    p = ok(w.preview(pi, lines=[(late, 3)]))
    assert issues(p) == {(late, 3): ["ATTACH_ORDER_BLOCKED"]}
    assert {(x["seq"], x["attach"]) for x in p["issues"][0]["blockers"]} == {(1, "1"), (2, "2")}
    w.generate(pi, lines=[(late, 2)])
    w.generate(pi, lines=[(late, 1)])
    w.generate(pi, lines=[(late, 3)])
    assert seqs(sql, late) == {2: [3, 4], 1: [5, 6], 3: [7, 8]}


def test_deleted_lines_do_not_block_and_item_unstarted_after_all_deleted(w, sql):
    """删过号的行不挡（重新生成接在最大号之后）；某项次的号全部删掉后回到没开始，又会被更小项次的额度挡。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 2, "1"), ("M2", 2, "2"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    w.generate(pa)
    # 删掉 A 项次 1 的号：项次 1 有额度但删过号，不挡后面的项次
    ok(w.delete(pa, lines=[(a, 1)], reason="t"))
    b = bill(w, pb, ("M1", 1, "2"), ("M2", 1, "3"))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    assert ok(w.preview(pb, lines=[(b, 1)]))["generatable"], "项次 2 已开始"
    assert ok(w.preview(pb, lines=[(b, 2)]))["issues"][0]["issues"] == ["ATTACH_ORDER_BLOCKED"], "项次 2 还没生成完"
    w.generate(pb, lines=[(b, 1)])
    w.generate(pb, lines=[(b, 2)])
    # A 项次 1 重新生成接在最大号之后（原流水 1、2 成为空号段）
    w.generate(pa, lines=[(a, 1)])
    assert seqs(sql, a) == {2: [3, 4], 1: [7, 8]}
    g = ok(w.api.get(f"/api/generate/gaps?pi={pb}", w.admin))
    assert [(x["start_seq"], x["end_seq"], x["bill_no"]) for x in g["segments"]] == [(1, 2, a)], (
        "空号段同前缀各 PI 都看得到"
    )
    # 项次 2 的号全部删掉：项次 2 回到没开始；这时新的项次 1 额度挡住它（删过号的行本身不挡）
    ok(w.delete(pa, lines=[(a, 2)], reason="t"))
    ok(w.delete(pb, lines=[(b, 1)], reason="t"))
    c = bill(w, pb, ("M1", 1, "1"))
    p = ok(w.preview(pa, lines=[(a, 2)]))
    assert issues(p) == {(a, 2): ["ATTACH_ORDER_BLOCKED"]}
    assert [x["bill_no"] for x in p["issues"][0]["blockers"]] == [c]


def test_empty_or_non_numeric_attach_blocks_all_unstarted(w, sql):
    """附件项次为空 / 不是数字：本行不能生成，并挡住这个前缀全部没开始的项次；已开始的项次不受影响。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 1, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    w.generate(pa)
    b = bill(w, pb, ("M1", 1, "A1"), ("M2", 1, "1"), ("M3", 1, "5"))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    p = ok(w.preview(pb, lines=[(b, 1)]))
    assert issues(p) == {(b, 1): ["ATTACH_NOT_NUMERIC"]}
    assert ok(w.preview(pb, lines=[(b, 2)]))["generatable"], "项次 1 已开始，不受影响"
    p = ok(w.preview(pb, lines=[(b, 3)]))
    assert issues(p) == {(b, 3): ["ATTACH_ORDER_BLOCKED"]}
    # 项次 1 已开始，但还有额度的项次 1 的行仍挡没开始的项次 5（项次 1 生成完才能开始项次 5）
    assert [(x["seq"], x["reason"]) for x in p["issues"][0]["blockers"]] == [
        (1, "ATTACH_NOT_NUMERIC"),
        (2, "NOT_GENERATED"),
    ]


def test_click_order_and_bill_order(w, sql):
    """点选物料行：同项次严格按点击先后（不按单据分组）；勾整单：同项次按单据勾选先后，单据内按行号。"""
    pre = P()
    pi = uid("PI")
    org = w.org(uid("O")[:10])
    a = bill(w, pi, ("M1", 1, "1"), ("M2", 1, "1"), ("M3", 1, "2"), org=org)
    b = bill(w, pi, ("M1", 1, "1"), ("M2", 1, "2"), org=org)
    ok(w.select(pi, [(a, 1)], custom=C(pre)))
    p = ok(w.preview(pi, lines=[(b, 2), (a, 2), (b, 1), (a, 3), (a, 1)]))
    assert [(x["bill_no"], x["seq"], x["start_seq"]) for x in p["items"]] == [
        (a, 2, 1),
        (b, 1, 2),
        (a, 1, 3),
        (b, 2, 4),
        (a, 3, 5),
    ]
    p = ok(w.preview(pi, bills=[b, a]))
    assert [(x["bill_no"], x["seq"]) for x in p["items"]] == [(b, 1), (a, 1), (a, 2), (b, 2), (a, 3)]


def test_confirm_shifts_when_other_pi_generated_first(w, sql):
    """预演后同前缀别的 PI 先生成：确认时自动顺延到新的最大号之后（shifted）；手动起止号内的号被占用时预演失效。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 2, "1"))
    b = bill(w, pb, ("M1", 3, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    pv = ok(w.preview(pa))
    assert (pv["start_seq"], pv["end_seq"]) == (1, 2)
    w.generate(pb)
    j = ok(w.api.post("/api/generate/run", m(token=pv["token"]), w.admin))
    assert j["status"] == "SUCCESS" and j["shifted"] is True
    assert (j["start_seq_dec"], j["end_seq_dec"]) == (4, 5)
    assert seqs(sql, a) == {1: [4, 5]}
    # 手动起止号：空号段被别的 PI 先用了，预演失效
    ok(w.delete(pb, lines=[(b, 1)], reason="t"))
    c = bill(w, pa, ("M2", 1, "1"))
    d = bill(w, pb, ("M2", 1, "1"))
    pv = ok(w.preview(pa, bills=[c], seq=(1, 1)))
    assert pv["items"][0]["segs"] == [[1, 1]]
    w.generate(pb, bills=[d], seq=(1, 1))
    assert w.api.post("/api/generate/run", m(token=pv["token"]), w.admin).code == "GEN_PREVIEW_CHANGED"


def test_pi_prefix_registered_inherited_and_locked(w, api, sql):
    """选规则登记在 PI 上：之后同步进来的单据直接用；没号时可改前缀；有号时锁定；号全删后又可改，
    原前缀上留下的流水成为空号段。自定义规则生成成功后才存入规则库。"""
    pi = uid("PI")
    org = w.org(uid("O")[:10])
    a = bill(w, pi, ("M1", 1, "1"), org=org)
    p1, p2 = P(), P()
    ok(w.select(pi, [(a, 1)], custom=C(p1)))
    assert sql.scalar("SELECT COUNT(*) FROM sn_rule WHERE rule_code LIKE %s", f"PI-{pi}-%") == 0, "生成前不存规则库"
    late = bill(w, pi, ("M2", 1, "1"), org=org)
    assert ok(w.preview(pi, bills=[late]))["generatable"], "新单据继承本 PI 的规则"
    ok(w.select(pi, [(a, 1)], custom=C(p2)))
    assert sql.scalar("SELECT prefix FROM sn_pi_rule WHERE pi_no = %s", pi) == p2, "还没号：可以改前缀"
    w.generate(pi)
    assert sql.scalar("SELECT rule_id FROM sn_pi_rule WHERE pi_no = %s", pi) is not None, "生成后存入规则库并登记"
    assert w.select(pi, [(a, 1)], custom=C(p1)).code == "RULE_PI_PREFIX_LOCKED"
    assert api.delete(f"/api/rules/pi-binding?pi={pi}", w.admin).code == "RULE_PI_PREFIX_LOCKED"
    ok(w.delete(pi, bills=[a, late], reason="t"))
    ok(w.select(pi, [(a, 1)], custom=C(p1)))
    other = uid("PI")
    o = bill(w, other, ("M1", 1, "1"))
    ok(w.select(other, [(o, 1)], custom=C(p2)))
    g = ok(api.get(f"/api/generate/gaps?pi={other}", w.admin))
    # 空号段按来源单据拆段
    assert (g["last_seq"], [(x["start_seq"], x["end_seq"], x["bill_no"]) for x in g["segments"]]) == (
        2,
        [(1, 1, a), (2, 2, late)],
    )


def test_seq_grow_is_per_prefix(w, api, sql):
    """自动突破按前缀：第一张 PI 选这个前缀时定下；之后别的 PI 选择不改；规则库可改；已突破后不能关闭。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 12, "1"))
    b = bill(w, pb, ("M1", 1, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre, seq_len=1, seq_grow=False)))
    assert sql.scalar("SELECT seq_grow FROM sn_prefix_counter WHERE prefix = %s", pre) == 0
    ok(w.select(pb, [(b, 1)], custom=C(pre, seq_len=1, seq_grow=True)))
    assert sql.scalar("SELECT seq_grow FROM sn_prefix_counter WHERE prefix = %s", pre) == 0, "第一张 PI 定下"
    assert w.preview(pa).code == "GEN_RULE_EXHAUSTED"
    ok(api.put("/api/rules/prefixes/grow", m(prefix=pre, seq_grow=True), w.admin))
    w.generate(pa)
    assert sql.scalar("SELECT last_seq_dec FROM sn_prefix_counter WHERE prefix = %s", pre) == 12
    r = api.put("/api/rules/prefixes/grow", m(prefix=pre, seq_grow=False), w.admin)
    assert r.code == "RULE_PREFIX_GROW_LOCKED"
    g = next(x for x in ok(api.get(f"/api/rules/prefixes?q={pre}", w.admin)).body if x["prefix"] == pre)
    assert (g["seq_grow"], g["top_seq"]) == (True, 12)
