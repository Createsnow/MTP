"""双 SN（2026-11-20）：PI 级开关；开启的 PI 每件同时生成第一 SN 与第二 SN（sn_item_sub），第二 SN 的规则另登记
（sn_pi_rule2，配置方式与第一 SN 相同），前缀用途分开（sn_prefix_role）；第二 SN 随号分配、交付（MES 明细 sn2）、
转移、删除；有号时开关锁定。"""

from __future__ import annotations

from conftest import m, ok, uid


def P() -> str:
    return "D" + uid("").replace("-", "")


def mk(w, *lines, org=None, customer="C1"):
    org = org or w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, customer, pi, *lines)
    return org, pi, bill


def custom(prefix: str, **kv) -> dict:
    return m(prefix=prefix, base=10, seq_len=4, with_length=False) | kv


def set_dual(w, pi: str, on: bool = True):
    return w.api.post("/api/generate/dual", m(pi_no=pi, dual=on), w.admin)


def select2(w, pi: str, lines, rule_id=None, custom=None):
    return w.api.post(
        "/api/rules/line-select",
        m(pi_no=pi, lines=[m(bill_no=b, seq=s) for b, s in lines], rule_id=rule_id, custom=custom, slot=2),
        w.admin,
    )


def pairs(sql, pi: str) -> list[tuple[str, str | None]]:
    return [
        (r["sn"], r["sn2"])
        for r in sql.all(
            "SELECT i.sn, s.sn2 FROM sn_item i LEFT JOIN sn_item_sub s ON s.item_id = i.id "
            "WHERE i.pi_no = %s ORDER BY i.bill_seq, i.seq_dec",
            pi,
        )
    ]


def dual_pi(w, *lines, first=None, second=None, org=None):
    """建一张双 SN 的 PI：开关、两个自定义规则。"""
    org, pi, bill = mk(w, *lines, org=org)
    ok(set_dual(w, pi))
    a, b = first or P(), second or P()
    ok(w.select(pi, [(bill, 1)], custom=custom(a)))
    ok(select2(w, pi, [(bill, 1)], custom=custom(b)))
    return org, pi, bill, a, b


def test_generate_pairs_first_and_second(w, api, sql):
    """数量 N 件生成 N 个第一 SN + N 个第二 SN，同一物料行内第 i 个对第 i 个；第二前缀登记为第二 SN 用。"""
    org, pi, bill, a, b = dual_pi(w, ("M1", 2, "100", "1"), ("M2", 3, "100", "2"))
    p = ok(w.preview(pi))
    assert p["generatable"], p
    assert p["qty"] == 5 and p["dual"] is True
    assert [x["sub"]["start_sn"] for x in p["items"]] == [f"{b}0001", f"{b}0003"]
    w.generate(pi)
    assert pairs(sql, pi) == [
        (f"{a}0001", f"{b}0001"),
        (f"{a}0002", f"{b}0002"),
        (f"{a}0003", f"{b}0003"),
        (f"{a}0004", f"{b}0004"),
        (f"{a}0005", f"{b}0005"),
    ]
    assert sql.scalar("SELECT role FROM sn_prefix_role WHERE prefix = %s", b) == 2
    assert sql.scalar("SELECT prefix FROM sn_prefix_role WHERE prefix = %s", a) is None
    assert sql.scalar("SELECT last_seq_dec FROM sn_prefix_counter WHERE prefix = %s", b) == 5
    # 计数按件
    assert sql.scalar("SELECT own_generated FROM sn_line_count WHERE bill_no = %s AND seq = 2", bill) == 3
    # 第二 SN 的规则生成后存入规则库，登记改记为规则
    rid2 = sql.scalar("SELECT rule_id FROM sn_pi_rule2 WHERE pi_no = %s", pi)
    assert rid2 and sql.scalar("SELECT prefix FROM sn_rule WHERE id = %s", rid2) == b


def test_single_pi_unchanged(w, api, sql):
    """没开双 SN 的 PI 与原来相同：不写第二 SN，计划里 sub 为 None。"""
    org, pi, bill = mk(w, ("M1", 2))
    ok(w.select(pi, [(bill, 1)], custom=custom(P())))
    p = ok(w.preview(pi))
    assert "dual" not in p.body and p["items"][0]["sub"] is None
    w.generate(pi)
    assert sql.count("SELECT COUNT(*) FROM sn_item_sub") == 0


def test_second_rule_required_and_dual_required(w, api, sql):
    """开了双 SN 没选第二规则：物料行 NO_RULE2、不能生成；没开双 SN 不能选第二 SN 规则。"""
    org, pi, bill = mk(w, ("M1", 2))
    ok(w.select(pi, [(bill, 1)], custom=custom(P())))
    assert select2(w, pi, [(bill, 1)], custom=custom(P())).code == "DUAL_NOT_ENABLED"
    ok(set_dual(w, pi))
    p = ok(w.preview(pi))
    assert not p["generatable"] and "NO_RULE2" in p["issues"][0]["issues"]
    ctx = ok(api.get(f"/api/generate/context?pi={pi}", w.admin))
    assert ctx["dual"]["dual"] is True and ctx["rule2"]["rule_prefix"]["prefix"] == "", "第二 SN 没有默认前缀"


def test_switch_locked_when_pi_has_numbers(w, api, sql):
    """有号时开、关都不行；全部删号后可以关闭，关闭时清除第二 SN 规则登记，重新生成为单 SN。"""
    org, pi, bill, a, b = dual_pi(w, ("M1", 2))
    w.generate(pi)
    assert set_dual(w, pi, False).code == "DUAL_LOCKED"
    ok(w.delete(pi, bills=[bill]))
    assert sql.count("SELECT COUNT(*) FROM sn_item_sub WHERE seq_prefix = %s", b) == 0
    assert sql.count("SELECT COUNT(*) FROM sn_item_sub_deleted WHERE seq_prefix = %s", b) == 2
    v = ok(set_dual(w, pi, False))
    assert v["dual"] is False and sql.count("SELECT COUNT(*) FROM sn_pi_rule2 WHERE pi_no = %s", pi) == 0
    w.generate(pi)
    assert [x[1] for x in pairs(sql, pi)] == [None, None]
    # 单 SN 有号时也不能开启
    assert set_dual(w, pi, True).code == "DUAL_LOCKED"


def test_prefix_roles_are_separate(w, api, sql):
    """第二 SN 用过的前缀不能当第一 SN；第一 SN 用过的前缀不能当第二 SN；第二前缀不能与本 PI 第一前缀互为开头。"""
    org, pi, bill, a, b = dual_pi(w, ("M1", 1))
    w.generate(pi)
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    assert w.select(p2, [(b2, 1)], custom=custom(b)).code == "RULE_PREFIX_ROLE_SECOND"
    ok(set_dual(w, p2))
    assert select2(w, p2, [(b2, 1)], custom=custom(a)).code == "RULE_PREFIX_ROLE_FIRST"
    c = P()
    ok(w.select(p2, [(b2, 1)], custom=custom(c)))
    assert select2(w, p2, [(b2, 1)], custom=custom(c + "X")).code == "RULE2_SAME_PREFIX"
    # 第二前缀可以给多张 PI 共用：接着共同的最大号编
    ok(select2(w, p2, [(b2, 1)], custom=custom(b)))
    w.generate(p2)
    assert pairs(sql, p2) == [(f"{c}0001", f"{b}0002")]
    opts = ok(api.get(f"/api/rules/options?pi={p2}&slot=2", w.admin)).body
    assert (
        {x["prefix"] for x in opts} >= {b}
        and a not in {x["prefix"] for x in opts}
        and c not in {x["prefix"] for x in opts}
    )
    opts1 = ok(api.get(f"/api/rules/options?pi={p2}", w.admin)).body
    assert b not in {x["prefix"] for x in opts1}


def test_push_pull_and_query_carry_sn2(w, api, sql):
    """推送、拉取、查询接口的明细带 sn2（customerCode 之后）；单 SN 的号为 null；批次枚数按件。"""
    from conftest import MES

    org = w.org(uid("O")[:10])
    w.enable_mes(org)
    _, pi, bill, a, b = dual_pi(w, ("M1", 3), org=org)
    w.generate(pi)
    w.allocate(pi)
    w.push()
    batch = next(x["batch"] for x in MES.received if x["batch"]["batchType"] == "NEW" and x["batch"]["pi"] == pi)
    assert batch["count"] == 3
    assert [(x["sn"], x["sn2"]) for x in batch["items"]] == pairs(sql, pi)
    assert list(batch["items"][0])[-1] == "sn2"
    assert MES.store[org][f"{a}0001"]["sn2"] == f"{b}0001"
    tok, _ = w.mes_account(org)
    got = ok(w.mes_delivered(tok, pi=pi))["items"]
    assert {x["sn"]: x["sn2"] for x in got} == dict(pairs(sql, pi))
    got = ok(w.mes_items(tok, batch["batchNo"]))["items"]
    assert {x["sn"]: x["sn2"] for x in got} == dict(pairs(sql, pi))
    # SN 查询：按第二 SN 也能查到，明细带第二 SN；导出与已分配下载都有「第二SN」列
    q = ok(api.get(f"/api/sn?sn={b}0002", w.admin))
    assert [(x["sn"], x["sn2"]) for x in q["items"]] == [(f"{a}0002", f"{b}0002")]


def test_bill_transfer_between_dual_pis_and_blocked_across(w, api, sql):
    """双 SN 的号转单到另一张双 SN 的 PI：第二 SN 随号走、不重新编号；单 SN 与双 SN 的 PI 之间不能转单。"""
    org = w.org(uid("O")[:10])
    _, pi, bill, a, b = dual_pi(w, ("M1", 2), org=org)
    w.generate(pi)
    w.allocate(pi)
    _, p2, b2, a2, c2 = dual_pi(w, ("M1", 2), org=org)
    _, p3, b3 = mk(w, ("M1", 2), org=org)
    body = m(pi_no=pi, bill_no=bill, scope="BILL", to_pi=p3, to_bill=b3, to_factory=org, to_material="M1", reason="x")
    assert api.post("/api/transfers/bill", body, w.admin).code == "TR_DUAL_MISMATCH"
    ok(api.post("/api/transfers/bill", body | m(to_pi=p2, to_bill=b2), w.admin))
    assert pairs(sql, p2) == [(f"{a}0001", f"{b}0001"), (f"{a}0002", f"{b}0002")]


def test_import_blocked_for_dual_pi_and_sn2_is_unique(w, api, sql):
    """双 SN 的 PI 不支持导入；导入的号与已有的第二 SN 相同时报「号池已有」。"""
    from app.modules.sn.services import sn_import

    org = w.org(uid("O")[:10])
    _, pi, bill, a, b = dual_pi(w, ("M1", 2), org=org)
    w.generate(pi)
    _, p2, b2 = mk(w, ("M1", 2, "100", "1"), org=org)
    rows = [
        {"pi": pi, "bill": bill, "sn": "X" + uid("")},
        {"pi": p2, "bill": b2, "sn": f"{b}0001"},
    ]
    issues = _import_issues(w, api, org, rows)
    assert "DUAL_PI" in issues[0] and sn_import.IN_POOL in issues[1]


def _import_issues(w, api, org, rows) -> list[set[str]]:
    import io

    from openpyxl import Workbook

    from app.modules.sn.services import sn_import

    wb = Workbook()
    ws = wb.active
    ws.append(sn_import.HEADERS)
    for r in rows:
        v = {
            "PI": r["pi"],
            "单据编号": r["bill"],
            "附件项次": "1",
            "物料编码": "M1",
            "ERP行号": 1,
            "完整 SN": r["sn"],
            "流水位数": 4,
            "进制位数": 10,
            "十进制流水号": "1",
            "米数": "100",
            "生产组织编号": org,
            "生产组织": f"组织{org}",
        }
        ws.append([v.get(h, "") for h in sn_import.HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    r = w.api.client.post(
        "/api/sn-import/check",
        files={"file": ("a.xlsx", buf.getvalue())},
        headers={"Authorization": f"Bearer {w.admin}"},
    )
    assert r.status_code == 200, r.text
    by_row = {x["row"]: {i["code"] for i in x["issues"]} for x in r.json()["issues"]}
    return [by_row.get(i + 2, set()) for i in range(len(rows))]


def test_second_prefix_attach_order_and_seq_start(w, api, sql):
    """第二前缀同样不拦没选中的行（更小项次没生成只提示，同一物料行只列一次）；
    第二 SN 可设起始号（从最大号之后编时第一个号不小于它）。"""
    org = w.org(uid("O")[:10])
    b = P()
    _, p1, b1, a1, _b = dual_pi(w, ("M1", 1, "100", "1"), ("M2", 1, "100", "2"), second=b, org=org)
    # 只生成项次 2：项次 1 还有额度，不拦，只提示
    p = ok(w.preview(p1, lines=[(b1, 2)]))
    assert p["generatable"] and p["lower_pending"] == {
        "total": 1,
        "items": [{"pi_no": p1, "bill_no": b1, "seq": 1, "attach": "1"}],
    }
    _, p2, b2 = mk(w, ("M1", 1, "100", "1"), org=org)
    ok(set_dual(w, p2))
    ok(w.select(p2, [(b2, 1)], custom=custom(P())))
    ok(select2(w, p2, [(b2, 1)], custom=custom(b, seq_start=50)))
    w.generate(p2)
    assert pairs(sql, p2)[0][1] == f"{b}0050"
    assert sql.scalar("SELECT seq_start FROM sn_pi_rule2 WHERE pi_no = %s", p2) is None, "起始号生成后清掉"


def test_pi_change_across_single_and_dual_cannot_be_confirmed(w, api, sql):
    """防呆：单据从双 SN 的 PI 改到单 SN 的 PI，PI 变更提醒不能确认。"""
    org = w.org(uid("O")[:10])
    _, pi, bill, a, b = dual_pi(w, ("M1", 1), org=org)
    w.generate(pi)
    _, p2, b2 = mk(w, ("M1", 1), org=org)
    w.touch(bill, pi=p2)
    ok(api.post("/api/orders/sync", m(bill_no=bill), w.admin))
    alert = sql.one("SELECT * FROM sn_order_alert WHERE bill_no = %s AND alert_type = 'PI' AND status = 'OPEN'", bill)
    assert alert is not None
    r = api.post(f"/api/orders/alerts/{alert['id']}/handle", m(action="CONFIRM"), w.admin)
    assert r.code == "ALERT_DUAL_MISMATCH"


def test_org_transfer_out_batch_and_out_list_carry_sn2(w, api, sql):
    """转组织：转入方改挂批次、原组织转出批次的明细都带 sn2；转出号清单有「第二SN」列。"""
    from conftest import MES, xlsx_rows

    a = w.org(uid("O")[:10])
    w.enable_mes(a)
    c = w.org(uid("O")[:10])
    w.enable_mes(c)
    _, pi, bill, x, y = dual_pi(w, ("M1", 2), org=a)
    w.generate(pi)
    w.allocate(pi)
    w.push()
    body = m(
        factory_code=a,
        pi_no=pi,
        bill_no=bill,
        scope="BILL",
        to_factory=c,
        to_pi=pi,
        to_bill=bill,
        to_material="M1",
        reason="调拨",
    )
    t = ok(api.post("/api/transfers/org/direct", body, w.admin))
    w.push()
    out = next(r["batch"] for r in MES.received if r["batch"]["batchType"] == "TRANSFER_OUT")
    assert {i["sn"]: i["sn2"] for i in out["items"]} == dict(pairs(sql, pi))
    assert MES.store[c][f"{x}0001"]["sn2"] == f"{y}0001"
    r = api.client.get(f"/api/transfers/org/{t['id']}/out-list", headers={"Authorization": f"Bearer {w.admin}"})
    rows = xlsx_rows(r.content)
    assert rows[0][1:3] == ["完整SN", "第二SN"] and {(z[1], z[2]) for z in rows[1:]} == set(pairs(sql, pi))
