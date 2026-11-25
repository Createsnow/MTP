"""箱号、托号模块模板（需求未定）：打开后示例接口经 sn.api 读 SN，受账户读范围限制；默认关闭时接口不存在。"""

from __future__ import annotations

import pytest
from conftest import Api, m, ok, uid
from fastapi.testclient import TestClient


@pytest.fixture
def api_with_templates(app_client, monkeypatch) -> Api:
    """同一个库、打开 box 与 pallet 的另一个应用实例（不跑启动流程，库已由会话级应用初始化）。"""
    from app.main import create_app

    monkeypatch.setenv("SN_MODULES", "org,order,sn,log,dashboard,box,pallet")
    return Api(TestClient(create_app(), raise_server_exceptions=False))


def test_templates_read_sn_through_sn_api(w, sql, api_with_templates):
    org = w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, ("M1", 2))
    ok(w.bind_pi(pi, w.rule("T" + uid("").replace("-", ""), seq_len=6)))
    w.generate(pi)
    sn = sql.scalar("SELECT sn FROM sn_item WHERE pi_no = %s ORDER BY seq_dec LIMIT 1", pi)
    api = api_with_templates
    got = ok(api.post("/api/box/template/sn-check", m(sns=[sn, "NOPE"]), w.admin))["items"]
    assert got == [
        {"sn": sn, "found": True, "status": "PENDING_ALLOC", "pi": pi, "bill_no": bill, "factory_code": None},
        {"sn": "NOPE", "found": False, "status": None, "pi": None, "bill_no": None, "factory_code": None},
    ]
    assert ok(api.post("/api/pallet/template/sn-check", m(sns=[sn, "NOPE"]), w.admin)).body == {
        "found": 1,
        "missing": ["NOPE"],
    }
    # 别的组织的普通操作员看不到
    other = w.org(uid("O")[:10])
    e = "B" + uid("").replace("-", "")[:10]
    tok = w.user(e, "operator", other)
    assert ok(api.post("/api/box/template/sn-check", m(sns=[sn]), tok))["items"][0]["found"] is False


def test_templates_absent_by_default(api, admin):
    assert api.post("/api/box/template/sn-check", m(sns=["X"]), admin).status == 404
    assert api.post("/api/pallet/template/sn-check", m(sns=["X"]), admin).status == 404
