"""共用前缀：一个前缀可以给多张 PI 用（流水部分须相同），号不重靠流水——后来的 PI 接在共同的最大号之后，
不用别的 PI 已用、已预留的流水（别的 PI 删号退回、不在预留里的空号可以用）。不共用前缀时与原来完全相同。"""

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
    """自定义规则用别的 PI 已用的前缀：同格式共用（本 PI 另存自己的自定义规则，流水接在后面）；
    进制或位数不同报格式锁定；规则库新建同前缀的规则照旧报前缀冲突。"""
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
    rid2 = sql.scalar("SELECT id FROM sn_rule WHERE rule_code = %s", f"PI-{p2}-1")
    assert sql.scalar("SELECT claimed_pi FROM sn_rule_version WHERE rule_id = %s", rid2) == p2
    body = m(rule_code=uid("R"), rule_name="x", category_value=uid("PI"), prefix=pre, base=10, seq_len=5)
    assert api.post("/api/rules", body, w.admin).code == "RULE_PREFIX_CONFLICT", "规则库保存：前缀不能与已有前缀相同"


def test_shared_prefix_gaps_skip_other_pi_used_and_reserved(w, api, sql):
    """共用前缀时空号段、手动起止号、所选空号段都不用别的 PI 已用、已预留的流水；别的 PI 跳过 / 退回的空号可以用，
    用了之后别的 PI 的空号段里也不再列出。"""
    org, p1, _b1 = mk(w, ("M1", 10))
    _, _, b1b = mk(w, ("M1", 2), pi=p1, org=org)
    _, p2, b2 = mk(w, ("M1", 5), org=org)
    _, _, b2b = mk(w, ("M1", 2), pi=p2, org=org)
    pre = P()
    rid = w.rule(pre, seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    w.generate(p1, bills=[_b1])
    # 手动起止号 21～22：11～20 成为 PI-1 跳过的空号（不在预留里）
    w.generate(p1, bills=[b1b], seq=(21, 22))
    g1 = ok(api.get(f"/api/generate/gaps?pi={p1}", w.admin))
    assert [(x["start_seq"], x["end_seq"]) for x in g1["segments"]] == [(11, 20)]
    ok(w.bind_pi(p2, rid))
    g2 = ok(api.get(f"/api/generate/gaps?pi={p2}", w.admin))
    assert (g2["last_seq"], g2["next_seq"]) == (22, 23), "最大号取共同的最大号"
    assert [(x["start_seq"], x["end_seq"]) for x in g2["segments"]] == [(11, 20)], "PI-1 已用、已预留的不列"
    # PI-1 已用的流水：手动起止号范围内不用（溢出到最大号之后），选作空号段报无效
    p = ok(w.preview(p2, bills=[b2], seq=(1, 5)))
    # 范围内都是 PI-1 的号：全部溢出，接在最大号之后（含本次新建的预留 23～29，原有逻辑）
    assert p["generatable"] and p["items"][0]["segs"] == [[30, 34]] and p["range_qty"] == 0
    assert w.preview(p2, bills=[b2], seq=(30, 30), gaps=[[3, 4]]).code == "GEN_GAP_INVALID"
    # PI-1 跳过的空号可以用
    w.generate(p2, bills=[b2], seq=(11, 15))
    assert seqs(sql, p2) == list(range(11, 16))
    # PI-2 用掉的 11～15 不再列给 PI-1；PI-2 这一行原预留 23～27 退回成 PI-2 的空号，PI-1 也可以用
    g1 = ok(api.get(f"/api/generate/gaps?pi={p1}", w.admin))
    assert [(x["start_seq"], x["end_seq"]) for x in g1["segments"]] == [(16, 20), (23, 27)]
    # PI-2 另一单据的预留 28～29 不被 PI-1 占：PI-1 按默认接在共同的最大号 29 之后，PI-2 的预演照常有效
    token = ok(w.preview(p2, bills=[b2b]))["token"]
    _, _, b1c = mk(w, ("M1", 3), pi=p1, org=org)
    w.generate(p1, bills=[b1c])
    assert seqs(sql, p1)[-3:] == [30, 31, 32]
    j = ok(api.post("/api/generate/run", m(token=token), w.admin))
    assert j["status"] == "SUCCESS" and seqs(sql, p2)[-2:] == [28, 29]
    # PI-2 新加的单据没有预留、接在最大号之后：预演后 PI-1 先接号，PI-2 的预演失效，重新预演接在后面
    _, _, b2c = mk(w, ("M1", 2), pi=p2, org=org)
    p = ok(w.preview(p2, bills=[b2c]))
    assert p["items"][0]["segs"] == [[33, 34]]
    _, _, b1d = mk(w, ("M1", 3), pi=p1, org=org)
    w.generate(p1, bills=[b1d])
    assert seqs(sql, p1)[-3:] == [33, 34, 35]
    assert api.post("/api/generate/run", m(token=p["token"]), w.admin).code == "GEN_PREVIEW_CHANGED"
    w.generate(p2, bills=[b2c])
    assert seqs(sql, p2)[-2:] == [36, 37]
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


def test_check_shared_rejects_plan_overlapping_other_pi(w, sql):
    """生成事务内的核对（拿到前缀锁后按最新已提交数据）：计划要占的流水被共用前缀的别的 PI 先用了，报预演已失效。"""
    import json

    from app.core.exceptions import BizError
    from app.services import generate

    org, p1, _ = mk(w, ("M1", 4))
    _, p2, b2 = mk(w, ("M1", 3), org=org)
    rid = w.rule(P(), seq_len=6, with_length=False)
    ok(w.bind_pi(p1, rid))
    w.generate(p1)
    ok(w.bind_pi(p2, rid))
    token = ok(w.preview(p2))["token"]
    plan = json.loads(sql.scalar("SELECT plan_json FROM sn_gen_preview WHERE token = %s", token))
    assert plan["shared_pis"] == [p1] and plan["items"][0]["segs"] == [[5, 7]]
    generate._check_shared(p2, plan)  # 还没有重叠
    _, _, b1b = mk(w, ("M1", 2), pi=p1, org=org)
    w.generate(p1, bills=[b1b])
    assert seqs(sql, p1)[-2:] == [5, 6]
    try:
        generate._check_shared(p2, plan)
        raise AssertionError("应报预演已失效")
    except BizError as e:
        assert e.code == "GEN_PREVIEW_CHANGED"


def test_concurrent_generation_on_shared_prefix_never_duplicates(w, sql):
    """共用前缀的两张 PI 同时按同一个最大号预演、同时确认生成：只有一张成功，另一张报预演已失效（不会撞号），
    重新预演后接在后面。"""
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
    ok_ = [r for r in out if r.status == 200 and r["status"] == "SUCCESS"]
    bad = [r for r in out if r not in ok_]
    assert len(ok_) == 1 and len(bad) == 1, [str(r) for r in out]
    assert bad[0].code == "GEN_PREVIEW_CHANGED" or (
        bad[0].status == 200 and bad[0]["error_code"] == "GEN_PREVIEW_CHANGED"
    ), str(bad[0])
    loser = p2 if ok_[0] is out[0] else p1
    w.generate(loser, bills=[b1b] if loser == p1 else None)
    no_duplicate_sn(sql, pre)
    assert sql.count("SELECT COUNT(*) FROM sn_item WHERE sn LIKE %s", pre + "%") == 4010
