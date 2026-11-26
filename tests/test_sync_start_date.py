"""同步起始日期（.env 的 SN_SYNC_START_DATE）：快照只收审核日期在起始日期及之后的已审核单据 + 已出号单据（不论日期）。

没设置时与原来完全相同（查询条件不变）；已出号单据照常比对（PI 变更、反审核、业务状态）；起始日期之前、没出过号的
单据移出快照，按单据同步也不收；日期不对时报错、快照不改。只涉及金蝶 → 本系统，不涉及 MES 接口。
"""

from __future__ import annotations

from datetime import date, timedelta

import conftest
from conftest import BILLS, m, ok, uid

#: 起始日期不能晚于今天：按当天往前推
_TODAY = date.today()
SINCE = (_TODAY - timedelta(days=3)).isoformat()
OLD = f"{(_TODAY - timedelta(days=10)).isoformat()}T09:30:00"
NEW = f"{(_TODAY - timedelta(days=3)).isoformat()}T08:00:00"


def P() -> str:
    return "P" + uid("").replace("-", "")


def approved(bill: str, when: str) -> None:
    BILLS[bill]["ApproveDate"] = when


def filters(path_part: str = "ExecuteBillQuery") -> list[str]:
    out = []
    for c in conftest.K3.calls:
        if path_part in c.path:
            data = conftest.FakeK3.param(c, 0)
            if data.get("FormId") == "PRD_MO":
                out.append(data["FilterString"])
    return out


def in_snapshot(sql, bill: str) -> int:
    return sql.count("SELECT COUNT(*) FROM sn_prd_mo WHERE bill_no = %s", bill)


def test_no_start_date_keeps_original_filters(w, sql, monkeypatch):
    """没设置：比对同步的表头、业务状态查询条件与原来完全相同（只有已审核）。"""
    monkeypatch.delenv("SN_SYNC_START_DATE", raising=False)
    org = w.org(uid("O")[:10])
    a = uid("MO")
    BILLS[a] = w.doc(a, org, "C1", "PI-A", ("M1", 5))
    conftest.K3.calls.clear()
    w.compare()
    assert in_snapshot(sql, a) == 1, "没有审核日期的单据照常进快照"
    fs = filters()
    assert fs[0] == "FDocumentStatus='C'", "表头"
    assert fs[-1] == "FDocumentStatus='C'", "业务状态"
    assert not any("FApproveDate" in f for f in fs)


def test_compare_scope_new_bills_plus_issued_old_bills(w, api, sql, monkeypatch):
    org = w.org(uid("O")[:10])
    old_plain, new_bill = uid("MO"), uid("MO")
    # 起始日期之前审核：一张没出号、一张已出号
    w.order(old_plain, org, "C1", "PI-OLD", ("M1", 5))
    approved(old_plain, OLD)
    pi = uid("PI")
    old_issued = uid("MO")
    w.order(old_issued, org, "C1", pi, ("M1", 4))
    approved(old_issued, OLD)
    ok(w.bind_pi(pi, w.rule(P())))
    w.generate(pi)
    BILLS[new_bill] = w.doc(new_bill, org, "C1", "PI-NEW", ("M1", 3))
    approved(new_bill, NEW)

    monkeypatch.setenv("SN_SYNC_START_DATE", SINCE)
    conftest.K3.calls.clear()
    r = w.compare()
    assert in_snapshot(sql, new_bill) == 1, "起始日期起审核的单据进快照"
    assert in_snapshot(sql, old_issued) == 1, "已出号单据不论日期留在快照"
    assert in_snapshot(sql, old_plain) == 0, "起始日期之前、没出过号的移出快照"
    assert r["removed"] == 1
    fs = filters()
    assert fs[0] == f"FDocumentStatus='C' AND FApproveDate>='{SINCE}'"
    assert any(f"FBillNo IN ('{old_issued}')" in f for f in fs), "已出号的旧单按单据编号另查"
    cfg = ok(api.get("/api/orders/sync-config", w.admin))
    assert (cfg["start_date"], cfg["start_date_ok"]) == (SINCE, True)

    # 已出号的旧单照常比对：PI 变更提醒、业务状态刷新
    BILLS[old_issued]["TreeEntity"][0].update(Status="5", PickMtrlStatus="2")
    p2 = uid("PI")
    w.touch(old_issued, pi=p2)
    w.compare()
    al = [
        x for x in ok(api.get(f"/api/orders/alerts?bill_no={old_issued}", w.admin))["items"] if x["alert_type"] == "PI"
    ]
    assert al and (al[0]["before_value"], al[0]["after_value"]) == (pi, p2)
    assert sql.one("SELECT biz_status, pick_status FROM sn_prd_mo WHERE bill_no = %s", old_issued) == {
        "biz_status": "5",
        "pick_status": "2",
    }
    # 反审核：照常移出并提醒
    w.touch(old_issued, status="D")
    w.compare()
    assert in_snapshot(sql, old_issued) == 0
    assert sql.scalar("SELECT leave_state FROM sn_bill_ledger WHERE bill_no = %s", old_issued) == "REVERSAL"

    # 清空起始日期：旧单重新拉回
    monkeypatch.delenv("SN_SYNC_START_DATE")
    w.compare()
    assert in_snapshot(sql, old_plain) == 1


def test_bill_sync_respects_start_date(w, api, sql, monkeypatch):
    org = w.org(uid("O")[:10])
    old, new = uid("MO"), uid("MO")
    BILLS[old] = w.doc(old, org, "C1", "PI-O", ("M1", 5))
    approved(old, OLD)
    BILLS[new] = w.doc(new, org, "C1", "PI-N", ("M1", 5))
    approved(new, NEW)
    monkeypatch.setenv("SN_SYNC_START_DATE", SINCE)
    r = ok(api.post("/api/orders/sync", m(bill_no=old), w.admin))
    assert (r["outcome"], r["since"]) == ("OUT_OF_RANGE", SINCE)
    assert in_snapshot(sql, old) == 0
    assert ok(api.post("/api/orders/sync", m(bill_no=new), w.admin))["outcome"] == "REPLACED"
    assert in_snapshot(sql, new) == 1
    # 没有审核日期的（批量查询同样拉不到）也不收
    del BILLS[old]["ApproveDate"]
    assert ok(api.post("/api/orders/sync", m(bill_no=old), w.admin))["outcome"] == "OUT_OF_RANGE"


def test_bill_sync_old_issued_bill_still_synced(w, api, sql, monkeypatch):
    org = w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, ("M1", 4))
    approved(bill, OLD)
    ok(w.bind_pi(pi, w.rule(P())))
    w.generate(pi)
    monkeypatch.setenv("SN_SYNC_START_DATE", SINCE)
    assert ok(api.post("/api/orders/sync", m(bill_no=bill), w.admin))["outcome"] == "REPLACED"
    assert in_snapshot(sql, bill) == 1


def test_rebuild_scope(w, api, sql, monkeypatch):
    org = w.org(uid("O")[:10])
    old_plain, new_bill = uid("MO"), uid("MO")
    w.order(old_plain, org, "C1", "PI-OLD", ("M1", 5))
    approved(old_plain, OLD)
    pi, old_issued = uid("PI"), uid("MO")
    w.order(old_issued, org, "C1", pi, ("M1", 4), ("M2", 2))
    approved(old_issued, OLD)
    ok(w.bind_pi(pi, w.rule(P())))
    w.generate(pi)
    BILLS[new_bill] = w.doc(new_bill, org, "C1", "PI-NEW", ("M1", 3))
    approved(new_bill, NEW)
    monkeypatch.setenv("SN_SYNC_START_DATE", SINCE)
    r = ok(api.post("/api/orders/rebuild", {}, w.admin))
    assert (r["since"], r["bills"], r["rows"]) == (SINCE, 2, 3)
    assert in_snapshot(sql, new_bill) == 1
    assert in_snapshot(sql, old_issued) == 2
    assert in_snapshot(sql, old_plain) == 0


def test_invalid_start_date_fails_without_touching_snapshot(w, api, sql, monkeypatch):
    org = w.org(uid("O")[:10])
    a = uid("MO")
    w.order(a, org, "C1", "PI-A", ("M1", 5))
    for bad in ("2026/11/26", "tomorrow", "2999-01-01"):
        monkeypatch.setenv("SN_SYNC_START_DATE", bad)
        r = api.post("/api/orders/compare", {}, w.admin)
        assert r.code == "SYNC_START_DATE_INVALID", bad
        assert in_snapshot(sql, a) == 1, "快照不改"
        assert api.post("/api/orders/sync", m(bill_no=a), w.admin).code == "SYNC_START_DATE_INVALID"
        assert api.post("/api/orders/rebuild", {}, w.admin).code == "SYNC_START_DATE_INVALID"
        cfg = ok(api.get("/api/orders/sync-config", w.admin))
        assert (cfg["start_date"], cfg["start_date_raw"], cfg["start_date_ok"]) == (None, bad, False)
    assert sql.scalar("SELECT last_ok FROM sn_sync_state WHERE id = 1") == 0, "比对同步记为失败，页面显示原因"
