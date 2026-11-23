"""以规则前缀为排号基准（2026-11-17 变更，取代按 PI 的号段预留）：
- 同一前缀下各 PI 共用一套十进制流水，接在前缀最大号之后；(seq_prefix, seq_dec) 唯一；
- 项次先后只管本次选中的范围（2026-11-23 放开）：没选中的单据、物料行（本 PI 或同前缀别的 PI）不拦，之后再选时
  接在前缀最大号之后；同前缀没选中的行里还有更小项次没生成时预演只提示（lower_pending）；删过号的行、失效的行不算；
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


def test_unselected_lines_do_not_block_only_hint(w, sql):
    """没选中的行不拦（本 PI 没选的行、同前缀别的 PI）：选哪些就生成哪些，接在前缀最大号之后；
    同前缀没选中的行里还有更小项次没生成时，预演给提示（lower_pending，不拦）。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 2, "1"), ("M2", 2, "2"))
    b = bill(w, pb, ("M1", 3, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    # A 只点选项次 2：不拦，提示本 PI 的项次 1 和 B 的项次 1 还没生成
    p = ok(w.preview(pa, lines=[(a, 2)]))
    assert p["generatable"] and not p["issues"]
    assert p["lower_pending"]["total"] == 2
    got = {(x["pi_no"], x["bill_no"], x["seq"], x["attach"]) for x in p["lower_pending"]["items"]}
    assert got == {(pa, a, 1, "1"), (pb, b, 1, "1")}
    # A 整单（项次 1、2 一起，项次 1 在前）：B 的项次 1 不拦，只提示
    p = ok(w.preview(pa, bills=[a]))
    assert p["generatable"] and [(x["seq"], x["start_seq"]) for x in p["items"]] == [(1, 1), (2, 3)]
    assert [(x["pi_no"], x["bill_no"]) for x in p["lower_pending"]["items"]] == [(pb, b)]
    # 生成页详情：物料行不带拦截原因
    c = ok(w.api.get(f"/api/generate/context?pi={pa}", w.admin))
    assert all(not x["issues"] for x in c["bills"][0]["lines"])
    w.generate(pa)
    # 之后再生成 B：接在最大号之后；B 已是最小项次，没有提示
    p = ok(w.preview(pb))
    assert p["lower_pending"] == {"total": 0, "items": []}
    w.generate(pb)
    assert seqs(sql, a) == {1: [1, 2], 2: [3, 4]}
    assert seqs(sql, b) == {1: [5, 6, 7]}


def test_late_lower_item_generates_after_max(w, sql):
    """后同步进来的单据有更小项次：哪个项次先选就先生成，都接在最大号之后（只提示，不拦）。"""
    pre = P()
    pi = uid("PI")
    org = w.org(uid("O")[:10])
    a = bill(w, pi, ("M1", 1, "1"), ("M2", 1, "2"), org=org)
    ok(w.select(pi, [(a, 1)], custom=C(pre)))
    w.generate(pi)
    late = bill(w, pi, ("M1", 2, "1"), ("M2", 2, "2"), ("M3", 2, "3"), org=org)
    p = ok(w.preview(pi, lines=[(late, 3)]))
    assert p["generatable"]
    assert {(x["seq"], x["attach"]) for x in p["lower_pending"]["items"]} == {(1, "1"), (2, "2")}
    w.generate(pi, lines=[(late, 3)])
    w.generate(pi, lines=[(late, 2)])
    w.generate(pi, lines=[(late, 1)])
    assert seqs(sql, late) == {3: [3, 4], 2: [5, 6], 1: [7, 8]}


def test_deleted_lines_regenerate_after_max_and_not_in_hint(w, sql):
    """删过号的行重新生成接在最大号之后（原流水成为空号段，同前缀各 PI 都看得到），也不算进提示。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 2, "1"), ("M2", 2, "2"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    w.generate(pa)
    ok(w.delete(pa, lines=[(a, 1)], reason="t"))
    b = bill(w, pb, ("M1", 1, "2"), ("M2", 1, "3"))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    p = ok(w.preview(pb, lines=[(b, 2)]))
    assert p["generatable"]
    assert [(x["bill_no"], x["seq"]) for x in p["lower_pending"]["items"]] == [(b, 1)], "删过号的 A 项次 1 不算"
    w.generate(pb, lines=[(b, 2)])
    w.generate(pb, lines=[(b, 1)])
    w.generate(pa, lines=[(a, 1)])
    assert seqs(sql, a) == {2: [3, 4], 1: [7, 8]}
    assert seqs(sql, b) == {2: [5], 1: [6]}
    g = ok(w.api.get(f"/api/generate/gaps?pi={pb}", w.admin))
    assert [(x["start_seq"], x["end_seq"], x["bill_no"]) for x in g["segments"]] == [(1, 2, a)], (
        "空号段同前缀各 PI 都看得到"
    )


def test_empty_or_non_numeric_attach_only_blocks_itself(w, sql):
    """附件项次为空 / 不是数字：本行不能生成（选中的行有一行不能生成，整次不能生成）；不挡没选中的行，也不算进提示。"""
    pre = P()
    pa, pb = uid("PI"), uid("PI")
    a = bill(w, pa, ("M1", 1, "1"))
    ok(w.select(pa, [(a, 1)], custom=C(pre)))
    w.generate(pa)
    b = bill(w, pb, ("M1", 1, "A1"), ("M2", 1, "1"), ("M3", 1, "5"))
    ok(w.select(pb, [(b, 1)], custom=C(pre)))
    assert issues(ok(w.preview(pb, lines=[(b, 1)]))) == {(b, 1): ["ATTACH_NOT_NUMERIC"]}
    p = ok(w.preview(pb, lines=[(b, 1), (b, 3)]))
    assert not p["generatable"] and issues(p) == {(b, 1): ["ATTACH_NOT_NUMERIC"]}
    p = ok(w.preview(pb, lines=[(b, 3)]))
    assert p["generatable"]
    assert [(x["seq"], x["attach"]) for x in p["lower_pending"]["items"]] == [(2, "1")], "不是数字的不算"


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
    原前缀上的号全删光：前缀最大号退回 0，不留空号段。自定义规则生成成功后才存入规则库。"""
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
    # 删的是前缀最后一段（全删光）：最大号退回 0，不留空号段，别的 PI 选这个前缀从 1 起
    assert (g["last_seq"], g["next_seq"], g["segments"]) == (0, 1, [])


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
