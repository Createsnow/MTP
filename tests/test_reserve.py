"""号段预留：同 PI 多单据（不同组织）分开生成也按附件项次接号；起始号平移、手动起止号放弃预留，
没有预留（后补附件项次、预留后才同步的单据）或预留不够的接在最大号之后（见 app/services/reserve.py）。

例子（与 PRD 相同）：单据 A（组织 1）行 1/2/3 附件项次 3/1/2，单据 B（组织 2）行 1/2/3 附件项次 2/4/1，各 10 枚，
先勾选 A（同一项次内 A 的行先排完再排 B），预留：
A-2 1～10、B-3 11～20、A-3 21～30、B-1 31～40、A-1 41～50、B-2 51～60。"""

from __future__ import annotations

from conftest import BILLS, m, ok, uid


def P() -> str:
    return "P" + uid("").replace("-", "")


def setup(w):
    """A、B 两张单据分属两个组织；都选同一个自定义规则（4 位），先由 A 单独生成。"""
    pi = uid("PI")
    a, b = uid("MO"), uid("MO")
    w.order(a, w.org(uid("O")[:10]), "C1", pi, ("M1", 10, "100", "3"), ("M2", 10, "100", "1"), ("M3", 10, "100", "2"))
    w.order(b, w.org(uid("O")[:10]), "C1", pi, ("M4", 10, "100", "2"), ("M5", 10, "100", "4"), ("M6", 10, "100", "1"))
    c = m(prefix=P(), base=10, seq_len=4, with_length=False)
    ok(w.select(pi, [(x, s) for x in (a, b) for s in (1, 2, 3)], custom=c))
    w.generate(pi, bills=[a])
    return pi, a, b, c


def seqs(sql, bill: str) -> dict[int, list[tuple[int, int]]]:
    """各物料行的号按流水连续合并：序号 → [(起, 止)]。"""
    out: dict[int, list[list[int]]] = {}
    for r in sql.all("SELECT bill_seq, seq_dec FROM sn_item WHERE bill_no = %s ORDER BY bill_seq, seq_dec", bill):
        g = out.setdefault(r["bill_seq"], [])
        if g and g[-1][1] + 1 == r["seq_dec"]:
            g[-1][1] = r["seq_dec"]
        else:
            g.append([r["seq_dec"], r["seq_dec"]])
    return {k: [tuple(x) for x in v] for k, v in out.items()}


def state(w, pi: str) -> tuple[list, list, int]:
    """（空号段、各物料行预留里空着的（单据, 序号, 起, 止）、最大号）。"""
    g = ok(w.api.get(f"/api/generate/gaps?pi={pi}", w.admin))
    return (
        [(x["start_seq"], x["end_seq"]) for x in g["segments"]],
        [(x["bill_no"], x["seq"], x["start_seq"], x["end_seq"]) for x in g["reserved"]],
        g["last_seq"],
    )


def test_separate_orgs_follow_attach_order(w, sql):
    """1. 不改起始号：A 先生成、B 后生成，整张 PI 1～60 按附件项次接号。"""
    pi, a, b, _c = setup(w)
    assert seqs(sql, a) == {2: [(1, 10)], 3: [(21, 30)], 1: [(41, 50)]}
    assert state(w, pi) == ([], [(b, 3, 11, 20), (b, 1, 31, 40), (b, 2, 51, 60)], 60), "B 没勾选也预留了位置"
    p = ok(w.preview(pi, bills=[b]))
    assert [(x["seq"], x["seq_src"], x["segs"], x["tail_qty"]) for x in p["items"]] == [
        (3, "RESERVE", [[11, 20]], 0),
        (1, "RESERVE", [[31, 40]], 0),
        (2, "RESERVE", [[51, 60]], 0),
    ]
    w.generate(pi, bills=[b])
    assert seqs(sql, b) == {3: [(11, 20)], 1: [(31, 40)], 2: [(51, 60)]}
    assert state(w, pi) == ([], [], 60)


def test_start_number_shifts_reserved_lines(w, sql):
    """2. B 起始号改成 5001：按「新号 = 预留号 − 预留起号 + 起始号」平移；原预留与跳过的流水成为空号段。"""
    pi, a, b, c = setup(w)
    ok(w.select(pi, [(b, 1), (b, 2), (b, 3)], custom={**c, "seq_start": 5001}))
    p = ok(w.preview(pi, bills=[b]))
    assert {x["seq"]: x["seq_src"] for x in p["items"]} == {1: "SHIFT", 2: "SHIFT", 3: "SHIFT"}
    w.generate(pi, bills=[b])
    assert seqs(sql, b) == {3: [(5011, 5020)], 1: [(5031, 5040)], 2: [(5051, 5060)]}
    assert state(w, pi) == ([(11, 20), (31, 40), (51, 5010), (5021, 5030), (5041, 5050)], [], 5060)


def test_start_number_shifts_single_line(w, sql):
    """3. 只勾选 B-2、起始号改成 5001：5051～5060；B-3、B-1 的预留照旧。"""
    pi, a, b, c = setup(w)
    ok(w.select(pi, [(b, 2)], custom={**c, "seq_start": 5001}))
    w.generate(pi, lines=[(b, 2)])
    assert seqs(sql, b) == {2: [(5051, 5060)]}
    assert state(w, pi) == ([(51, 5050)], [(b, 3, 11, 20), (b, 1, 31, 40)], 5060)
    # 平移后与已有的号重叠：不能生成
    ok(w.select(pi, [(b, 3)], custom={**c, "seq_start": 11}))
    r = w.preview(pi, lines=[(b, 3)])
    assert r.code == "GEN_SHIFT_CONFLICT", "11～20 平移到 21～30，与 A-3 的号重叠"


def test_range_rest_after_max_or_into_chosen_gaps(w, sql):
    """4. 只勾选 B-3、手动起止号 11～15：B-3 放弃预留；剩下的 5 枚
    a. 默认接在最大号之后（61～65，跳过 B-2 的预留 51～60），原预留 16～20 成为空号段；
    b. 选空号段 16～20（本行原预留）：正好用完；
    c. 选更大的空号段 100～199：用 100～104，105～199 仍是空号段。"""
    pi, a, b, c = setup(w)
    p = ok(w.preview(pi, lines=[(b, 3)], seq=(11, 15)))
    x = p["items"][0]
    assert (x["seq_src"], x["segs"], p["range_qty"], p["gap_qty"], p["overflow_qty"], p["tail_from"]) == (
        "RANGE",
        [[11, 15], [61, 65]],
        5,
        0,
        5,
        61,
    )
    p = ok(w.preview(pi, lines=[(b, 3)], seq=(11, 15), gaps=[[16, 20]]))
    assert (p["items"][0]["segs"], p["range_qty"], p["gap_qty"], p["overflow_qty"]) == ([[11, 20]], 5, 5, 0)
    # 别的物料行的预留、已有号不能选作空号段
    assert w.preview(pi, lines=[(b, 3)], seq=(11, 15), gaps=[[21, 25]]).code == "GEN_GAP_INVALID"
    assert w.preview(pi, lines=[(b, 3)], seq=(11, 15), gaps=[[1, 5]]).code == "GEN_GAP_INVALID"
    # c：先让 B-2 平移到 5051～5060，51～5050 成为空号段
    ok(w.select(pi, [(b, 2)], custom={**c, "seq_start": 5001}))
    w.generate(pi, lines=[(b, 2)])
    w.generate(pi, lines=[(b, 3)], seq=(11, 15), gaps=[[100, 199]])
    assert seqs(sql, b)[3] == [(11, 15), (100, 104)]
    assert state(w, pi) == ([(16, 20), (51, 99), (105, 5050)], [(b, 1, 31, 40)], 5060)


def test_range_rest_default_after_max(w, sql):
    """4a. 默认：剩下的接在最大号之后，B-3 原预留空着的 16～20 退回成空号段。"""
    pi, a, b, _c = setup(w)
    w.generate(pi, lines=[(b, 3)], seq=(11, 15))
    assert seqs(sql, b) == {3: [(11, 15), (61, 65)]}
    assert state(w, pi) == ([(16, 20)], [(b, 1, 31, 40), (b, 2, 51, 60)], 65)


def test_unreserved_lines_append_after_max(w, api, sql):
    """没有附件项次的行先不预留，补了项次再生成时接在最后（tail_qty 提示）；预留之后才同步进来的单据同样接在最后；
    数量调小时多出的预留退回成空号段。"""
    pi = uid("PI")
    a, b = uid("MO"), uid("MO")
    org = w.org(uid("O")[:10])
    w.order(a, org, "C1", pi, ("M1", 10, "100", "2"), ("M2", 10, "100", "5"))
    w.order(b, w.org(uid("O")[:10]), "C1", pi, ("M3", 10, "100", ""))
    c = m(prefix=P(), base=10, seq_len=4, with_length=False)
    ok(w.select(pi, [(a, 1), (a, 2), (b, 1)], custom=c))
    w.generate(pi, lines=[(a, 1)])
    assert state(w, pi) == ([], [(a, 2, 11, 20)], 20), "B-1 没有附件项次，不预留"
    ok(api.put("/api/orders/attach", m(bill_no=b, seq=1, value="1"), w.admin))
    p = ok(w.preview(pi, bills=[b]))
    assert [(x["seq_src"], x["segs"], x["tail_qty"]) for x in p["items"]] == [("TAIL", [[21, 30]], 10)]
    w.generate(pi, bills=[b])
    # 预留之后才同步进来的单据：接在最后
    d = uid("MO")
    w.order(d, org, "C1", pi, ("M4", 5, "100", "1"))
    ok(w.select(pi, [(d, 1)], custom=c))
    j = w.generate(pi, bills=[d])
    assert (j["start_seq_dec"], j["end_seq_dec"]) == (31, 35)
    # A-2 数量从 10 调成 6：生成后预留 17～20 退回成空号段
    BILLS[a]["TreeEntity"][1]["Qty"] = 6.0
    w.touch(a)
    w.compare()
    w.generate(pi, lines=[(a, 2)])
    assert seqs(sql, a)[2] == [(11, 16)]
    assert state(w, pi)[:2] == ([(17, 20)], [])


def test_context_rule_first_sn_and_reserve_start(w, api):
    """生成页：counter.reserve_start 为预留起号；规则列的第一个号按预留（起始号平移时按平移后的）。"""
    pi, a, b, c = setup(w)
    ctx = ok(api.get(f"/api/generate/context?pi={pi}", w.admin))
    assert ctx["counter"]["reserve_start"] == 1
    first = {x["seq"]: x["rule"]["first_sn"] for bb in ctx["bills"] if bb["bill_no"] == b for x in bb["lines"]}
    assert first == {1: f"{c['prefix']}0031", 2: f"{c['prefix']}0051", 3: f"{c['prefix']}0011"}
    ok(w.select(pi, [(b, 2)], custom={**c, "seq_start": 5001}))
    ctx = ok(api.get(f"/api/generate/context?pi={pi}", w.admin))
    line = next(x for bb in ctx["bills"] if bb["bill_no"] == b for x in bb["lines"] if x["seq"] == 2)
    assert line["rule"]["first_sn"] == f"{c['prefix']}5051"


def test_material_map_moves_reserve_with_counts(w, api, sql):
    """失去对应「确认对应」到新行：原行的预留号段随计数、号一起归新行；之后在新行删号再生成回到原号段，
    原行（已不存在）不再留着一段没人能用的预留。"""
    pi, a, b, c = setup(w)
    assert seqs(sql, a)[1] == [(41, 50)]
    BILLS[a]["TreeEntity"][0]["MaterialId"] = {"Number": "M9", "Name": "x"}
    BILLS[a]["TreeEntity"].append(
        {"Seq": 4, "MaterialId": {"Number": "M9", "Name": "x"}, "Qty": 10.0, "Flength": "100", "FTERM": "3"}
    )
    w.touch(a)
    w.compare()
    al = [x for x in ok(api.get(f"/api/orders/alerts?bill_no={a}", w.admin))["items"] if x["alert_type"] == "MATERIAL"]
    ok(api.post(f"/api/orders/alerts/{al[0]['id']}/handle", m(action="MAP", target_seq=4), w.admin))
    assert seqs(sql, a)[4] == [(41, 50)]
    got = sql.all("SELECT seq, start_seq, end_seq FROM sn_pi_reserve WHERE pi_no = %s AND bill_no = %s", pi, a)
    assert {(r["seq"], r["start_seq"], r["end_seq"]) for r in got if r["start_seq"] == 41} == {(4, 41, 50)}
    ok(w.delete(pi, lines=[(a, 4)]))
    ok(w.select(pi, [(a, 4)], custom=c))
    w.generate(pi, lines=[(a, 4)])
    assert seqs(sql, a)[4] == [(41, 50)], "删号后再生成回到原号段"


def mk3(w):
    """与生成页截图相同的三张单据（同一组织）：592 行1/2 项次 1/2；-001 行1 项次 2；-002 行1/2 项次 1/2。"""
    pi = uid("PI")
    org = w.org(uid("O")[:10])
    base = uid("ADT")
    m592, m001, m002 = base, base + "-001", base + "-002"
    w.order(m002, org, "C1", pi, ("FC1", 144, "100", "1"), ("FC2", 144, "100", "2"))
    w.order(m592, org, "C1", pi, ("FC1", 1356, "100", "1"), ("FC2", 1316, "100", "2"))
    w.order(m001, org, "C1", pi, ("FC2", 40, "100", "2"))
    c = m(prefix=P(), base=10, seq_len=4, with_length=False)
    ok(w.select(pi, [(m592, 1), (m592, 2), (m001, 1), (m002, 1), (m002, 2)], custom=c))
    return pi, m592, m001, m002


def ranges(sql, pi: str) -> list[tuple[str, int, int, int]]:
    """本 PI 各物料行的号（按流水）：[(单据, 序号, 起, 止)]。"""
    return [
        (r["bill_no"], r["bill_seq"], r["s"], r["e"])
        for r in sql.all(
            "SELECT bill_no, bill_seq, MIN(seq_dec) s, MAX(seq_dec) e FROM sn_item WHERE pi_no = %s "
            "GROUP BY bill_no, bill_seq ORDER BY s",
            pi,
        )
    ]


def test_all_deleted_regenerate_follows_new_pick_order(w, sql):
    """整张 PI 的号全部删掉后再生成（默认）：丢弃旧预留，按本次点选先后从原预留起号重排；最大号不变、没有空号段。"""
    pi, a, b, c2 = mk3(w)
    w.generate(pi, bills=[c2, a, b])
    assert ranges(sql, pi) == [
        (c2, 1, 1, 144),
        (a, 1, 145, 1500),
        (c2, 2, 1501, 1644),
        (a, 2, 1645, 2960),
        (b, 1, 2961, 3000),
    ]
    ok(w.delete(pi, bills=[c2, a, b]))
    assert state(w, pi)[1][0] == (c2, 1, 1, 144), "删号后预留照旧显示"
    p = ok(w.preview(pi, bills=[a, c2, b]))
    assert [(x["bill_no"], x["seq"], x["segs"]) for x in p["items"]] == [
        (a, 1, [[1, 1356]]),
        (c2, 1, [[1357, 1500]]),
        (a, 2, [[1501, 2816]]),
        (c2, 2, [[2817, 2960]]),
        (b, 1, [[2961, 3000]]),
    ]
    w.generate(pi, bills=[a, c2, b])
    assert ranges(sql, pi) == [
        (a, 1, 1, 1356),
        (c2, 1, 1357, 1500),
        (a, 2, 1501, 2816),
        (c2, 2, 2817, 2960),
        (b, 1, 2961, 3000),
    ]
    assert state(w, pi) == ([], [], 3000)
    got = sql.all("SELECT bill_no, seq, start_seq, end_seq FROM sn_pi_reserve WHERE pi_no = %s ORDER BY start_seq", pi)
    assert [(r["bill_no"], r["seq"], r["start_seq"], r["end_seq"]) for r in got] == ranges(sql, pi), "预留按新顺序重建"


def test_all_deleted_partial_regenerate_reserves_rest_in_new_order(w, sql):
    """号全部删掉后只点选部分物料行：整张 PI 按本次点选先后（没点的单据按单据编号在后）重排预留，没点的行用新预留。"""
    pi, a, b, c2 = mk3(w)
    w.generate(pi, bills=[c2, a, b])
    ok(w.delete(pi, bills=[c2, a, b]))
    w.generate(pi, lines=[(b, 1), (c2, 2)])
    assert ranges(sql, pi) == [(b, 1, 1501, 1540), (c2, 2, 1541, 1684)]
    _gaps, res, last = state(w, pi)
    assert res == [(c2, 1, 1, 144), (a, 1, 145, 1500), (a, 2, 1685, 3000)] and last == 3000, (
        "本次点选的单据 -001、-002 在前，没点的 592 在后"
    )
    w.generate(pi, bills=[a, c2])
    assert state(w, pi) == ([], [], 3000)


def test_partly_deleted_keeps_reservation(w, sql):
    """PI 还有号（只删了部分单据）：预留不重排，再生成回到原号段，与点选先后无关。"""
    pi, a, b, c2 = mk3(w)
    w.generate(pi, bills=[c2, a, b])
    before = ranges(sql, pi)
    ok(w.delete(pi, bills=[c2, a]))
    w.generate(pi, bills=[a, c2])
    assert ranges(sql, pi) == before


def test_all_deleted_range_keeps_reservation(w, sql):
    """号全部删掉后用手动起止号：不重排预留（照旧：本次的行放弃预留，不占别的行的预留）。"""
    pi, a, b, c2 = mk3(w)
    w.generate(pi, bills=[c2, a, b])
    ok(w.delete(pi, bills=[c2, a, b]))
    w.generate(pi, lines=[(b, 1)], seq=(1, 40))
    assert ranges(sql, pi) == [(b, 1, 3001, 3040)], "1～40 是 -002 行1 的预留，不能用，接在最大号之后"
    assert (c2, 1, 1, 144) in state(w, pi)[1]


def test_all_deleted_context_first_sn_from_reserve_start(w, api):
    """号全部删掉后，生成页规则列的「第一个号」都按原预留起号估算（下次按点选先后重排）。"""
    pi, a, b, c2 = mk3(w)
    w.generate(pi, bills=[c2, a, b])
    ok(w.delete(pi, bills=[c2, a, b]))
    ctx = ok(api.get(f"/api/generate/context?pi={pi}", w.admin))
    firsts = {x["rule"]["first_sn"][-4:] for bb in ctx["bills"] for x in bb["lines"]}
    assert firsts == {"0001"} and ctx["counter"]["reserve_start"] == 1
