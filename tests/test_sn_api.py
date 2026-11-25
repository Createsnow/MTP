"""SN 模块对外接口 app/modules/sn/api.py 的契约（docs/模块化单体改造方案.md 第 7.1 节）：箱号、托号等模块只通过它读 SN。

改签名、返回字段或语义（读范围、不存在的号不返回、大小写、数量上限）须同步改调用方并更新本文件。
"""

from __future__ import annotations

import dataclasses

import pytest
from conftest import m, ok, uid

from app.core.exceptions import BizError
from app.core.security import PAGE, CurrentUser
from app.modules.sn import api as sn_api

ADMIN = CurrentUser.system()


def op(*factories: str) -> CurrentUser:
    return CurrentUser(1, "op", "op", "operator", factories[0] if factories else None, PAGE, None, tuple(factories))


def setup(w, qty: int = 3):
    org = w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, ("M1", qty, "0100"))
    ok(w.bind_pi(pi, w.rule("A" + uid("").replace("-", ""), seq_len=6)))
    w.generate(pi)
    return org, pi, bill


def test_fields_are_the_contract():
    assert [f.name for f in dataclasses.fields(sn_api.SnInfo)] == [
        "sn",
        "sn2",
        "status",
        "suspend",
        "pi",
        "bill_no",
        "bill_seq",
        "material_code",
        "customer_code",
        "factory_code",
    ]
    assert (sn_api.PENDING_ALLOC, sn_api.ALLOCATED, sn_api.TO_PUSH, sn_api.PUSHED) == (
        "PENDING_ALLOC",
        "ALLOCATED",
        "TO_PUSH",
        "PUSHED",
    )


def test_sn_info_pending_then_allocated(w, sql):
    org, pi, bill = setup(w)
    sns = [r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE pi_no = %s ORDER BY seq_dec", pi)]
    got = sn_api.sn_info(ADMIN, [*sns, "NOT-EXISTS", " ", ""])
    assert list(got) == sns, "不存在的、空值不返回"
    x = got[sns[0]]
    assert (x.status, x.suspend, x.pi, x.bill_no, x.bill_seq, x.material_code, x.customer_code, x.factory_code) == (
        "PENDING_ALLOC",
        None,
        pi,
        bill,
        1,
        "M1",
        "C1",
        None,
    )
    assert x.sn2 is None
    w.allocate(pi)
    got = sn_api.sn_info(ADMIN, [f"  {sns[0]}  ", sns[0].lower()])
    assert list(got) == [sns[0]], "前后空白去掉；大小写不同不算同一枚"
    assert (got[sns[0]].status, got[sns[0]].factory_code) == ("ALLOCATED", org)


def test_sn_info_read_scope(w, sql):
    org, pi, _ = setup(w, qty=2)
    other = w.org(uid("O")[:10])
    sns = [r["sn"] for r in sql.all("SELECT sn FROM sn_item WHERE pi_no = %s", pi)]
    assert len(sn_api.sn_info(op(org), sns)) == 2, "待分配的号按所在单据的组织"
    assert sn_api.sn_info(op(other), sns) == {}, "范围外与不存在一样不返回"
    w.allocate(pi)
    assert len(sn_api.sn_info(op(org), sns)) == 2
    assert sn_api.sn_info(op(other), sns) == {}


def test_sn_info_dual_and_limit(w, sql):
    org = w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, ("M1", 2))
    ok(w.api.post("/api/generate/dual", m(pi_no=pi, dual=True), w.admin))
    for slot, prefix in ((1, "D" + uid("").replace("-", "")), (2, "E" + uid("").replace("-", ""))):
        custom = m(prefix=prefix, base=10, seq_len=4, with_length=False)
        sel = m(pi_no=pi, lines=[m(bill_no=bill, seq=1)], custom=custom, slot=slot)
        ok(w.api.post("/api/rules/line-select", sel, w.admin))
    w.generate(pi)
    pairs = sql.all("SELECT i.sn, s.sn2 FROM sn_item i JOIN sn_item_sub s ON s.item_id = i.id WHERE i.pi_no = %s", pi)
    got = sn_api.sn_info(ADMIN, [p["sn"] for p in pairs])
    assert {k: v.sn2 for k, v in got.items()} == {p["sn"]: p["sn2"] for p in pairs}
    assert sn_api.sn_info(ADMIN, []) == {}
    with pytest.raises(BizError):
        sn_api.sn_info(ADMIN, [f"X{i}" for i in range(sn_api.MAX_SNS + 1)])
