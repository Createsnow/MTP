"""共用前缀：一个前缀可以给多张 PI 用（流水部分须相同），同一前缀下各 PI 共用一套流水——接在前缀最大号之后，
不用别的 PI 已用的流水（删号腾出、跳过的空号同前缀各 PI 都能用）；同前缀的生成由前缀锁串行，确认时自动顺延。"""

from __future__ import annotations

import time

from conftest import MES, m, ok, uid


def mk(w, *lines, pi=None, org=None, bill=None):
    org = org or w.org(uid("O")[:10])
    pi = pi or uid("PI")
    bill = bill or uid("MO")
    w.order(bill, org, "C1", pi, *lines)
    return org, pi, bill


def P() -> str:
    return "S" + uid("").replace("-", "")


def seqs(sql, pi: str) -> list[int]:
    return [int(r["seq_dec"]) for r in sql.all("SELECT seq_dec FROM sn_item WHERE seq_pi_no = %s ORDER BY seq_dec", pi)]


def no_duplicate_sn(sql, prefix: str) -> None:
    rows = sql.all("SELECT sn FROM sn_item WHERE sn LIKE %s", prefix + "%")
    assert len(rows) == len({r["sn"] for r in rows})


def test_shared_prefix_library_rule_continues_after_other_pi(w, api, sql):
    """PI-1 先用规则生成 1～10；PI-2 选同一条规则不填新前缀即共用前缀，从 11 起；PI-1 之后补号接在 16 之后（号不连续）。
    PI-2 另有归属自己的同前缀版本（不是上限自动突破版本），再生成沿用它。MES 报文结构不变，serialPi 为生成它的 PI。"""
    org = w.org(uid("O")[:10])
    w.enable_mes(org, batch_size=50)
    _, p1, _b1 = mk(w, ("M1", 10), org=org)
    _, p2, _b2 = mk(w, ("M1", 5), org=org)
    pre = P()
    rid = w.rule(pre, seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    w.generate(p1)
    r = ok(w.bind_pi(p2, rid))["rule"]
    assert (r["plan"], r["prefix"], r["other_pi"]) == ("SHARE", pre, p1), "不填新前缀：共用前缀"
    assert ok(api.get(f"/api/generate/context?pi={p2}", w.admin))["counter"]["next_seq"] == 11
    w.generate(p2)
    assert seqs(sql, p2) == list(range(11, 16))
    assert sql.scalar("SELECT MIN(sn) FROM sn_item WHERE pi_no = %s", p2) == f"{pre}000011"
    v1 = sql.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = 1", rid)
    v2 = sql.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND claimed_pi = %s", rid, p2)
    assert (v2["prefix"], v2["seq_len"], v2["derived_from"], v2["used"]) == (pre, 6, v1["id"], 1)
    assert sql.scalar("SELECT current_version FROM sn_rule WHERE id = %s", rid) == 1, "不改规则的当前版本"
    opt = next(x for x in ok(api.get(f"/api/rules/options?pi={p2}", w.admin)).body if x["rule_id"] == rid)
    assert opt["plan"] == "USE", "之后沿用本 PI 的同前缀版本"
    # PI-2 再生成：沿用同一版本，不另出
    _, _, b2b = mk(w, ("M1", 2), pi=p2, org=org)
    w.generate(p2, bills=[b2b])
    assert seqs(sql, p2) == list(range(11, 18))
    assert sql.count("SELECT COUNT(*) FROM sn_rule_version WHERE rule_id = %s", rid) == 2
    # PI-1 补号：接在共同的最大号 17 之后
    _, _, b1b = mk(w, ("M1", 3), pi=p1, org=org)
    w.generate(p1, bills=[b1b])
    assert seqs(sql, p1) == list(range(1, 11)) + [18, 19, 20]
    no_duplicate_sn(sql, pre)
    # MES 报文：字段不变，serialPi 为生成它的 PI
    w.allocate(p2)
    w.push()
    items = [i for x in MES.received if x["batch"].get("pi") == p2 for i in x["batch"]["items"]]
    first = min(items, key=lambda i: int(i["serialDec"]))
    assert (first["sn"], first["serial"], first["serialDec"], first["serialPi"], first["pi"]) == (
        f"{pre}000011",
        "000011",
        "11",
        p2,
        p2,
    )


def test_shared_prefix_custom_rule_and_format_lock(w, api, sql):
    """自定义规则用别的 PI 已用的前缀：共用前缀，归到这个前缀的规则下（后缀不同时为本 PI 另出版本，不另建规则），
    流水接在后面；进制或位数不同报格式锁定；规则库新建同前缀的规则照旧报前缀冲突。"""
    _, p1, b1 = mk(w, ("M1", 4))
    _, p2, b2 = mk(w, ("M1", 3))
    pre = P()
    c = m(prefix=pre, base=10, seq_len=5, with_length=False)
    ok(w.select(p1, [(b1, 1)], custom=c))
    w.generate(p1)
    assert w.select(p2, [(b2, 1)], custom={**c, "seq_len": 6}).code == "RULE_PREFIX_FORMAT_LOCKED"
    assert w.select(p2, [(b2, 1)], custom={**c, "base": 16}).code == "RULE_PREFIX_FORMAT_LOCKED"
    ok(w.select(p2, [(b2, 1)], custom={**c, "suffix": "Z"}))
    w.generate(p2)
    assert [r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE pi_no = %s ORDER BY seq_dec", p2)] == [
        f"{pre}{i:05d}Z" for i in (5, 6, 7)
    ]
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE rule_code = %s", f"PI-{p2}-1") == 0, "一个前缀一条规则"
    rid = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre)
    assert sql.scalar("SELECT suffix FROM sn_rule_version WHERE rule_id = %s AND claimed_pi = %s", rid, p2) == "Z"
    body = m(rule_code=uid("R"), rule_name="x", category_value=uid("PI"), prefix=pre, base=10, seq_len=5)
    assert api.post("/api/rules", body, w.admin).code == "RULE_PREFIX_CONFLICT", "规则库保存：前缀不能与已有前缀相同"


def test_shared_prefix_gaps_skip_other_pi_used(w, api, sql):
    """同一前缀的空号段、手动起止号、所选空号段按前缀算：别的 PI 已用的流水不能用，跳过 / 删掉腾出的空号同前缀各 PI
    都能用，用了之后别的 PI 的空号段里也不再列出；预演后别的 PI 先接号，确认时自动顺延。"""
    org, p1, _b1 = mk(w, ("M1", 10))
    _, _, b1b = mk(w, ("M1", 2), pi=p1, org=org)
    _, p2, b2 = mk(w, ("M1", 5), org=org)
    _, _, b2b = mk(w, ("M1", 2), pi=p2, org=org)
    pre = P()
    rid = w.rule(pre, seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    w.generate(p1, bills=[_b1])
    # 手动起止号 21～22：11～20 成为跳过的空号
    w.generate(p1, bills=[b1b], seq=(21, 22))
    g1 = ok(api.get(f"/api/generate/gaps?pi={p1}", w.admin))
    assert [(x["start_seq"], x["end_seq"]) for x in g1["segments"]] == [(11, 20)]
    ok(w.bind_pi(p2, rid))
    g2 = ok(api.get(f"/api/generate/gaps?pi={p2}", w.admin))
    assert (g2["last_seq"], g2["next_seq"]) == (22, 23), "最大号为前缀最大号"
    assert [(x["start_seq"], x["end_seq"]) for x in g2["segments"]] == [(11, 20)]
    # 范围内都是 PI-1 的号：全部溢出，接在最大号之后；选作空号段报无效
    p = ok(w.preview(p2, bills=[b2], seq=(1, 5)))
    assert p["generatable"] and p["items"][0]["segs"] == [[23, 27]] and p["range_qty"] == 0
    assert w.preview(p2, bills=[b2], seq=(30, 30), gaps=[[3, 4]]).code == "GEN_GAP_INVALID"
    # 跳过的空号可以用
    w.generate(p2, bills=[b2], seq=(11, 15))
    assert seqs(sql, p2) == list(range(11, 16))
    g1 = ok(api.get(f"/api/generate/gaps?pi={p1}", w.admin))
    assert [(x["start_seq"], x["end_seq"]) for x in g1["segments"]] == [(16, 20)]
    # 预演后 PI-1 先接号：PI-2 确认时自动顺延到新的最大号之后
    token = ok(w.preview(p2, bills=[b2b]))["token"]
    _, _, b1c = mk(w, ("M1", 3), pi=p1, org=org)
    w.generate(p1, bills=[b1c])
    assert seqs(sql, p1)[-3:] == [23, 24, 25]
    j = ok(api.post("/api/generate/run", m(token=token), w.admin))
    assert (j["status"], j["shifted"]) == ("SUCCESS", True) and seqs(sql, p2)[-2:] == [26, 27]
    no_duplicate_sn(sql, pre)


def test_shared_prefix_rebuild_after_delete_skips_other_pi(w, sql):
    """PI-2 的号全部删掉后重排预留：从原预留起号重排会占到 PI-1 的流水时，改从共同的最大号之后排。"""
    org, p1, b1 = mk(w, ("M1", 10))
    _, p2, b2 = mk(w, ("M1", 5), org=org)
    pre = P()
    rid = w.rule(pre, seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    w.generate(p1)
    ok(w.bind_pi(p2, rid))
    w.generate(p2)
    assert seqs(sql, p2) == list(range(11, 16))
    _, _, b1b = mk(w, ("M1", 10), pi=p1, org=org)
    w.generate(p1, bills=[b1b])
    assert seqs(sql, p1)[-10:] == list(range(16, 26))
    ok(w.delete(p2, bills=[b2], reason="t"))
    _, _, b2b = mk(w, ("M1", 5), pi=p2, org=org)
    w.generate(p2)
    assert seqs(sql, p2) == list(range(26, 36)), "11～20 会占到 PI-1 的 16～20：改从 26 起"
    no_duplicate_sn(sql, pre)


def test_not_shared_prefix_unchanged(w, api, sql):
    """不共用前缀：最大号、空号段、接号与原来相同（各 PI 各自从 1 起）。"""
    _, p1, _ = mk(w, ("M1", 3))
    _, p2, _ = mk(w, ("M1", 2))
    ok(w.bind_pi(p1, w.rule(P(), seq_len=6, with_length=False)))
    ok(w.bind_pi(p2, w.rule(P(), seq_len=6, with_length=False)))
    w.generate(p1)
    w.generate(p2)
    assert (seqs(sql, p1), seqs(sql, p2)) == ([1, 2, 3], [1, 2])
    g = ok(api.get(f"/api/generate/gaps?pi={p2}", w.admin))
    assert (g["last_seq"], g["next_seq"], g["segments"]) == (2, 3, [])


def test_prefix_lock_taken_once_per_transaction(app_client):
    """同一事务里再拿同一前缀的前缀锁不等自己（生成事务开头已拿，规则落库时 check_prefix 再拿）；别的事务拿不到，
    事务结束后释放。"""
    from app.db.session import db
    from app.services import locks

    p = P()
    t0 = time.monotonic()
    with db.tx():
        locks.hold_prefixes([p])
        locks.hold_prefix(p)
        locks.hold_prefixes([p, p + "x"])
        other = locks.try_acquire("prefix", [p], 0)
        assert other is None, "别的连接拿不到"
    assert time.monotonic() - t0 < 5
    h = locks.try_acquire("prefix", [p, p + "x"], 0)
    assert h is not None, "事务结束后已释放"
    h.release()


def test_confirm_rejected_when_attach_order_changed(w, sql):
    """确认生成时在前缀锁内按最新数据重算：预演后同前缀别的 PI 选了这个前缀、带更小项次的行，项次拦截变了，
    报预演已失效（不会顺延着生成）。"""
    org, p1, b1 = mk(w, ("M1", 2, "100", "2"))
    rid = w.rule(P(), seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    token = ok(w.preview(p1))["token"]
    _, p2, _b2 = mk(w, ("M1", 2, "100", "1"), org=org)
    ok(w.bind_pi(p2, rid))
    assert w.api.post("/api/generate/run", m(token=token), w.admin).code == "GEN_PREVIEW_CHANGED"
    assert sql.count("SELECT COUNT(*) FROM sn_item WHERE bill_no = %s", b1) == 0


def test_concurrent_generation_on_shared_prefix_never_duplicates(w, sql):
    """同一前缀的两张 PI 同时按同一个最大号预演、同时确认生成：前缀锁让两笔串行，后到的按最新的最大号自动顺延，
    两张都成功、不会撞号。"""
    import threading

    org, p1, _ = mk(w, ("M1", 10))
    _, p2, _ = mk(w, ("M1", 2000), org=org)
    pre = P()
    rid = w.rule(pre, seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    w.generate(p1)
    ok(w.bind_pi(p2, rid))
    _, _, b1b = mk(w, ("M1", 2000), pi=p1, org=org)
    t1 = ok(w.preview(p1, bills=[b1b]))
    t2 = ok(w.preview(p2))
    assert t1["items"][0]["segs"][0][0] == t2["items"][0]["segs"][0][0] == 11, "两边都从 11 起"
    start = threading.Barrier(2)
    out: list = [None, None]

    def run(i: int, token: str) -> None:
        start.wait()
        out[i] = w.api.post("/api/generate/run", m(token=token), w.admin)

    ths = [threading.Thread(target=run, args=(i, t["token"])) for i, t in enumerate((t1, t2))]
    for t in ths:
        t.start()
    for t in ths:
        t.join(120)
    assert all(r.status == 200 and r["status"] == "SUCCESS" for r in out), [str(r) for r in out]
    assert sorted(bool(r["shifted"]) for r in out) == [False, True], "后到的一张自动顺延"
    no_duplicate_sn(sql, pre)
    assert sql.count("SELECT COUNT(*) FROM sn_item WHERE sn LIKE %s", pre + "%") == 4010
