"""一个前缀一条规则（2026-11-19）：sn_rule.prefix 唯一；自定义规则用已有前缀时归到那个前缀的规则下（米数、单位、后缀
与规则不同时为本 PI 另出版本，不另建规则）；页面版本号只按后缀区分；生成过号的规则不能改前缀与流水格式。"""

from __future__ import annotations

import re

from conftest import m, ok, uid


def P() -> str:
    return "W" + uid("").replace("-", "")


def mk(w, *lines, org=None, customer="C1"):
    org = org or w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, customer, pi, *lines)
    return org, pi, bill


def custom(prefix: str, **kv) -> dict:
    return m(prefix=prefix, base=10, seq_len=4, with_length=False) | kv


def sns(sql, pi: str) -> list[str]:
    return [r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE seq_pi_no = %s ORDER BY seq_dec", pi)]


def test_custom_on_existing_prefix_attaches_to_its_rule(w, api, sql):
    """PI-1 用自定义规则建了前缀；PI-2 用同一前缀但带单位、PI-3 改了后缀：都归到这条规则下（不另建规则），
    流水共用；版本号只按后缀区分（PI-2 仍是 v1，PI-3 是 v2）。"""
    org = w.org(uid("O")[:10])
    pre = P()
    _, p1, b1 = mk(w, ("M1", 2, "100", "1", "M"), org=org)
    _, p2, b2 = mk(w, ("M1", 2, "100", "1", "M"), org=org)
    _, p3, b3 = mk(w, ("M1", 1, "100", "1", "M"), org=org)
    ok(w.select(p1, [(b1, 1)], custom=custom(pre)))
    w.generate(p1)
    rid = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre)
    assert rid and re.fullmatch(r"[0-9A-F]{8}", sql.scalar("SELECT rule_code FROM sn_rule WHERE id = %s", rid))
    r = ok(w.select(p2, [(b2, 1)], custom=custom(pre, with_unit=True)))["rule"]
    assert r["plan"] == "NEW", "格式与规则不同：按自定义登记，生成时归到前缀的规则下"
    w.generate(p2)
    assert sns(sql, p2) == [f"{pre}0003M", f"{pre}0004M"], "同前缀共用流水，本 PI 带单位"
    ok(w.select(p3, [(b3, 1)], custom=custom(pre, suffix="X")))
    w.generate(p3)
    assert sns(sql, p3) == [f"{pre}0005X"]
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE prefix = %s", pre) == 1, "一个前缀一条规则"
    rows = {v["claimed_pi"]: v for v in sql.all("SELECT * FROM sn_rule_version WHERE rule_id = %s", rid)}
    assert set(rows) == {p1, p2, p3}
    assert (rows[p2]["with_unit"], rows[p3]["suffix"]) == (1, "X")
    assert {
        x["pi_no"]: x["rule_id"] for x in sql.all("SELECT * FROM sn_pi_rule WHERE pi_no IN (%s, %s, %s)", p1, p2, p3)
    } == {
        p1: rid,
        p2: rid,
        p3: rid,
    }, "PI 登记都改记为这条规则"

    def opt(pi: str) -> dict:
        return next(x for x in ok(api.get(f"/api/rules/options?pi={pi}", w.admin)).body if x["rule_id"] == rid)

    assert (opt(p1)["version"], opt(p2)["version"], opt(p3)["version"]) == (1, 1, 2), "版本号只按后缀区分"
    assert opt(p2)["custom"]["with_unit"] is True and opt(p2)["prefix"] == pre, "点选时按本 PI 用的配置填进自定义"
    # 本 PI 再生成沿用自己的版本（米数、单位跟随本 PI）
    _, p4, b4 = mk(w, ("M1", 1, "100", "1", "M"), org=org)
    assert ok(w.select(p4, [(b4, 1)], rule_id=rid))["rule"]["plan"] == "SHARE"
    w.generate(p4)
    assert sns(sql, p4) == [f"{pre}0006"], "规则库选用：按规则当前版本（第一张 PI 的配置）"


def test_library_rule_prefix_and_seq_format_locked_after_use(w, api, sql):
    """规则生成过号后：改前缀、进制、流水位数、字符表报 RULE_USED_FORMAT_LOCKED；改米数 / 后缀另出一版作为当前版本，
    尚未归属，只影响之后新选它的 PI（已有自己版本的 PI 不变）；新建规则前缀不能与已有的相同。"""
    org = w.org(uid("O")[:10])
    pre = P()
    rid = w.rule(pre, seq_len=4, with_length=False)
    _, p1, b1 = mk(w, ("M1", 1), org=org)
    ok(w.select(p1, [(b1, 1)], rule_id=rid))
    w.generate(p1)
    for body in (
        m(rule_name="x", prefix=P(), base=10, seq_len=4, with_length=False),
        m(rule_name="x", prefix=pre, base=16, seq_len=4, with_length=False),
        m(rule_name="x", prefix=pre, base=10, seq_len=5, with_length=False),
    ):
        assert api.put(f"/api/rules/{rid}", body, w.admin).code == "RULE_USED_FORMAT_LOCKED", body
    r = ok(
        api.put(
            f"/api/rules/{rid}",
            m(rule_name="x", prefix=pre, base=10, seq_len=4, with_length=False, suffix="Z"),
            w.admin,
        )
    )
    assert r["action"] == "NEW_VERSION" and r["rule"]["current"]["display_version"] == 2
    cur = sql.one(
        "SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = %s", rid, r["rule"]["rule"]["current_version"]
    )
    assert cur["claimed_pi"] is None and cur["suffix"] == "Z"
    # 已有自己版本的 PI 不变
    w.order(b1, org, "C1", p1, ("M1", 2))
    w.generate(p1)
    assert sns(sql, p1) == [f"{pre}0001", f"{pre}0002"]
    # 新选它的 PI 首次归属新的当前版本
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    assert ok(w.select(p2, [(b2, 1)], rule_id=rid))["rule"]["plan"] == "CLAIM"
    w.generate(p2)
    assert sns(sql, p2) == [f"{pre}0003Z"]
    dup = m(rule_code=uid("R"), rule_name="dup", prefix=pre, base=10, seq_len=4)
    assert api.post("/api/rules", dup, w.admin).code == "RULE_PREFIX_CONFLICT"


def test_unused_library_rule_prefix_taken_by_custom_attaches(w, api, sql):
    """规则库里建好、还没用过的规则（前缀 XYZ）：PI 在自定义里用 XYZ（格式相同）即登记为这条规则；格式不同（带米数）
    生成时归到它下面另出版本，规则的当前版本仍未归属。"""
    org = w.org(uid("O")[:10])
    pre = P()
    rid = w.rule(pre, seq_len=4, with_length=False)
    _, p1, b1 = mk(w, ("M1", 1), org=org)
    r = ok(w.select(p1, [(b1, 1)], custom=custom(pre)))["rule"]
    assert (r["rule_id"], r["plan"]) == (rid, "CLAIM")
    _, p2, b2 = mk(w, ("M1", 1, "100"), org=org)
    ok(w.select(p2, [(b2, 1)], custom=custom(pre, with_length=True, sep_before="-")))
    w.generate(p2)
    assert sns(sql, p2) == [f"{pre}0001-100"]
    assert sql.scalar("SELECT claimed_pi FROM sn_rule_version WHERE rule_id = %s AND version = 1", rid) is None
    assert sql.scalar("SELECT rule_id FROM sn_pi_rule WHERE pi_no = %s", p2) == rid
    w.generate(p1)
    assert sns(sql, p1) == [f"{pre}0002"]


# ---------------------------------------------------------------------- 前缀已腾空（2026-11-25）


def _ctx(api, w, pi: str) -> dict:
    return ok(api.get(f"/api/generate/context?pi={pi}", w.admin)).body


def test_released_prefix_allows_format_change_after_delete(w, api, sql):
    """本 PI 号全删掉、没有别的 PI 用过或登记过：同前缀可以改流水位数（不再提示须换新前缀），生成按新格式；
    规则的当前版本换成新格式，之后别的 PI 选这条规则按新格式共用；再删光可以改回原格式。"""
    org = w.org(uid("O")[:10])
    pre = P()
    _, pi, b = mk(w, ("M1", 2), org=org)
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    w.generate(pi)
    assert _ctx(api, w, pi)["prefix_locked"].get(pre), "有号时格式锁定"
    ok(w.delete(pi, bills=[b]))
    assert pre not in _ctx(api, w, pi)["prefix_locked"], "前缀已腾空：格式不锁定"
    ok(w.select(pi, [(b, 1)], custom=custom(pre, seq_len=6)))
    w.generate(pi)
    assert sns(sql, pi) == [f"{pre}000001", f"{pre}000002"]
    rid = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre)
    assert sql.count("SELECT COUNT(*) FROM sn_rule WHERE prefix = %s", pre) == 1, "仍是一个前缀一条规则"
    cur = sql.one(
        "SELECT v.* FROM sn_rule_version v JOIN sn_rule r ON r.id = v.rule_id AND v.version = r.current_version "
        "WHERE r.id = %s",
        rid,
    )
    assert (cur["seq_len"], cur["claimed_pi"], cur["derived_from"]) == (6, pi, None), "当前版本换成新格式"
    # 有号后又锁定：改回 4 位须换新前缀
    assert w.select(pi, [(b, 1)], custom=custom(pre)).code == "RULE_PREFIX_FORMAT_LOCKED"
    # 别的 PI 选这条规则：按新格式共用，流水接续
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    ok(w.select(p2, [(b2, 1)], rule_id=rid))
    w.generate(p2)
    assert sns(sql, p2) == [f"{pre}000003"]
    # 别的 PI 用旧格式：锁定
    _, p3, b3 = mk(w, ("M1", 1), org=org)
    assert w.select(p3, [(b3, 1)], custom=custom(pre)).code == "RULE_PREFIX_FORMAT_LOCKED"


def test_released_prefix_revert_format(w, api, sql):
    """前缀腾空后换格式，再删光改回原格式：沿用原来那一版，当前版本改回它。"""
    pre = P()
    _, pi, b = mk(w, ("M1", 2))
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    w.generate(pi)
    ok(w.delete(pi, bills=[b]))
    ok(w.select(pi, [(b, 1)], custom=custom(pre, seq_len=6)))
    w.generate(pi)
    ok(w.delete(pi, bills=[b]))
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    w.generate(pi)
    assert sns(sql, pi) == [f"{pre}0001", f"{pre}0002"]
    rid = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre)
    assert sql.scalar("SELECT current_version FROM sn_rule WHERE id = %s", rid) == 1
    assert sql.count("SELECT COUNT(*) FROM sn_rule_version WHERE rule_id = %s", rid) == 2, "沿用原来那一版"


def test_prefix_not_released_when_other_pi_used_or_registered(w, api, sql):
    """别的 PI 用过（号还在）或登记过这个前缀：本 PI 号全删掉后格式仍锁定，前缀仍挡住互为开头的前缀。"""
    org = w.org(uid("O")[:10])
    pre = P()
    _, p1, b1 = mk(w, ("M1", 2), org=org)
    _, p2, b2 = mk(w, ("M1", 2), org=org)
    ok(w.select(p1, [(b1, 1)], custom=custom(pre)))
    w.generate(p1)
    # 别的 PI 只登记、没生成
    ok(w.select(p2, [(b2, 1)], custom=custom(pre)))
    ok(w.delete(p1, bills=[b1]))
    assert _ctx(api, w, p1)["prefix_locked"].get(pre), "别的 PI 登记过：仍锁定"
    assert w.select(p1, [(b1, 1)], custom=custom(pre, seq_len=6)).code == "RULE_PREFIX_FORMAT_LOCKED"
    assert w.select(p1, [(b1, 1)], custom=custom(pre + "X")).code == "RULE_PREFIX_OVERLAP"
    # 别的 PI 生成了（号还在）
    w.generate(p2)
    assert w.select(p1, [(b1, 1)], custom=custom(pre, seq_len=6)).code == "RULE_PREFIX_FORMAT_LOCKED"
    assert w.select(p1, [(b1, 1)], custom=custom(pre + "X")).code == "RULE_PREFIX_OVERLAP"


def test_released_prefix_allows_overlapping_prefix(w, api, sql):
    """本 PI 号全删掉后可以改用与旧前缀一个是另一个开头的前缀；之后别的 PI 不能再用旧前缀（与新前缀互为开头）。"""
    org = w.org(uid("O")[:10])
    pre = P()
    _, pi, b = mk(w, ("M1", 2), org=org)
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    w.generate(pi)
    ok(w.delete(pi, bills=[b]))
    ok(w.select(pi, [(b, 1)], custom=custom(pre + "X")))
    w.generate(pi)
    assert sns(sql, pi) == [f"{pre}X0001", f"{pre}X0002"]
    rid = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre)
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    assert w.select(p2, [(b2, 1)], rule_id=rid).code == "RULE_PREFIX_OVERLAP"
    assert w.select(p2, [(b2, 1)], custom=custom(pre)).code == "RULE_PREFIX_OVERLAP"
    # 本 PI 有号时也不能回到旧前缀
    assert w.select(pi, [(b, 1)], custom=custom(pre)).code == "RULE_PI_PREFIX_LOCKED"


def test_abandoned_prefix_does_not_block_sharing_new_prefix(w, api, sql):
    """本 PI 前缀腾空后改用与旧前缀互为开头的新前缀：旧前缀没有号、也没有 PI 登记（已废弃），不挡别的 PI 共用新前缀
    （按规则、按自定义都可以，流水接续）；之后本 PI 要回到旧前缀，与还在用的新前缀互为开头，照常挡住。"""
    org = w.org(uid("O")[:10])
    pre = P()
    _, p1, b1 = mk(w, ("M1", 2), org=org)
    ok(w.select(p1, [(b1, 1)], custom=custom(pre)))
    w.generate(p1)
    ok(w.delete(p1, bills=[b1]))
    ok(w.select(p1, [(b1, 1)], custom=custom(pre + "X")))
    w.generate(p1)
    rid = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre + "X")
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    ok(w.select(p2, [(b2, 1)], rule_id=rid))
    w.generate(p2)
    assert sns(sql, p2) == [f"{pre}X0003"], "共用新前缀，流水接续"
    _, p3, b3 = mk(w, ("M1", 1), org=org)
    ok(w.select(p3, [(b3, 1)], custom=custom(pre + "X")))
    w.generate(p3)
    assert sns(sql, p3) == [f"{pre}X0004"]
    # 旧前缀仍不能给别的 PI 用
    old = sql.scalar("SELECT id FROM sn_rule WHERE prefix = %s", pre)
    _, p4, b4 = mk(w, ("M1", 1), org=org)
    assert w.select(p4, [(b4, 1)], rule_id=old).code == "RULE_PREFIX_OVERLAP"
    assert w.select(p4, [(b4, 1)], custom=custom(pre)).code == "RULE_PREFIX_OVERLAP"
    # 本 PI 删光自己的号后要回到旧前缀（本 PI 已有的版本）：别的 PI 还在用新前缀，挡住
    ok(w.delete(p1, bills=[b1]))
    assert w.select(p1, [(b1, 1)], rule_id=old).code == "RULE_PREFIX_OVERLAP"
    assert w.select(p1, [(b1, 1)], custom=custom(pre)).code == "RULE_PREFIX_OVERLAP"


def test_rule_select_shares_pi_lock(w, api, sql):
    """选规则、登记、解除登记与生成共用 PI 操作锁：生成进行中改选会看不到还没提交的号，把登记改成别的前缀。"""
    from app.platform import locks

    pre = P()
    _, pi, b = mk(w, ("M1", 1))
    ok(w.select(pi, [(b, 1)], custom=custom(pre)))
    rid = w.rule(P(), seq_len=4, with_length=False)
    held = locks.pi_lock(pi)
    try:
        assert w.select(pi, [(b, 1)], custom=custom(pre + "Y")).code == "PI_BUSY"
        assert w.bind_pi(pi, rid).code == "PI_BUSY"
        assert w.api.delete(f"/api/rules/pi-binding?pi={pi}", w.admin).code == "PI_BUSY"
    finally:
        held.release()
    assert sql.scalar("SELECT prefix FROM sn_pi_rule WHERE pi_no = %s", pi) == pre
    ok(w.select(pi, [(b, 1)], custom=custom(pre + "Y")))
