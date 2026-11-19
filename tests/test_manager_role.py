"""管理员角色：授权组织内普通操作员全部权限 + 审计 + 管理授权组织内的普通操作员、审计员账户。"""

from __future__ import annotations

from conftest import emp, m, ok, uid
from test_role_scope import P


def _id(sql, emp_no: str) -> int:
    return sql.scalar("SELECT id FROM sn_user WHERE emp_no = %s", emp_no)


def test_manager_has_operator_and_audit_pages_but_no_global_config(w, api):
    org = w.org(uid("O")[:10])
    mg = w.user(emp("M"), "manager", org)
    for path in (
        "/api/orders",
        "/api/generate/pis",
        "/api/allocation/bills",
        "/api/rules",
        "/api/transfers",
        "/api/delivery/batches",
        "/api/export/bills",
        "/api/sn",
        "/api/dashboard",
        "/api/audits",
        "/api/audits/meta",
        "/api/users",
    ):
        assert api.get(path, mg).status == 200, path
    assert api.post("/api/factories/sync", {}, mg).code == "FORBIDDEN", "生产组织配置只给系统管理员"
    assert api.post("/api/orders/rebuild", {}, mg).code == "FORBIDDEN", "全量重建快照只给系统管理员"
    me = ok(api.get("/api/auth/me", mg))
    assert me["role"] == "manager" and me["factory_codes"] == [org] and not me["super_admin"]


def test_manager_requires_factory_and_single_session(w, api):
    org = w.org(uid("O")[:10])
    r = api.post(
        "/api/users", m(emp_no=emp(), name="x", role="manager", factory_codes=[], password="Pass1234"), w.admin
    )
    assert r.code == "USER_FACTORY_REQUIRED"
    e = emp("M")
    first = w.user(e, "manager", org)
    ok(api.page_login(e, "Pass1234"))
    assert api.get("/api/dashboard", first).code == "SESSION_REPLACED", "管理员和普通操作员一样单页面登录"


def test_manager_manages_operators_and_auditors_within_scope(w, api, sql):
    a, b = w.org(uid("O")[:10]), w.org(uid("O")[:10])
    mg = w.user(emp("M"), "manager", a)
    # 新建：只能普通操作员、审计员，授权组织不能超出自己的范围
    e = emp("F")
    r = ok(api.post("/api/users", m(emp_no=e, name="x", role="operator", factory_codes=[a], password="Pass1234"), mg))
    assert r["role"] == "operator" and r["factory_codes"] == [a]
    q = emp("Q")
    ok(api.post("/api/users", m(emp_no=q, name="x", role="auditor", factory_codes=[a], password="Pass1234"), mg))
    for body in (
        m(emp_no=emp(), name="x", role="operator", factory_codes=[b], password="Pass1234"),
        m(emp_no=emp(), name="x", role="operator", factory_codes=[a, b], password="Pass1234"),
        m(emp_no=emp(), name="x", role="manager", factory_codes=[a], password="Pass1234"),
        m(emp_no=emp(), name="x", role="mes", factory_code=a),
    ):
        assert api.post("/api/users", body, mg).code == "USER_MANAGE_OUT_OF_SCOPE", body
    assert api.post("/api/users", m(emp_no=emp(), name="x", role="admin", password="Pass1234"), mg).code in (
        "USER_ADMIN_UNIQUE",
        "USER_MANAGE_OUT_OF_SCOPE",
    )
    fid = _id(sql, e)
    # 修改：可改姓名、在操作员 / 审计员间改角色；不能改成管理员，不能授权范围外的组织
    r = ok(api.put(f"/api/users/{fid}", m(name="y", role="auditor", factory_codes=[a]), mg))
    assert r["name"] == "y" and r["role"] == "auditor"
    assert api.put(f"/api/users/{fid}", m(name="y", role="manager", factory_codes=[a]), mg).code == (
        "USER_MANAGE_OUT_OF_SCOPE"
    )
    assert api.put(f"/api/users/{fid}", m(name="y", role="operator", factory_codes=[a, b]), mg).code == (
        "USER_MANAGE_OUT_OF_SCOPE"
    )
    # 停用、启用、重置密码
    assert ok(api.post(f"/api/users/{fid}/disable", {}, mg))["status"] == "DISABLED"
    assert ok(api.post(f"/api/users/{fid}/enable", {}, mg))["status"] == "ACTIVE"
    ok(api.post(f"/api/users/{fid}/reset-password", m(password="Newp1234"), mg))
    ok(api.page_login(e, "Newp1234"))


def test_manager_cannot_touch_accounts_out_of_scope(w, api, sql):
    a, b = w.org(uid("O")[:10]), w.org(uid("O")[:10])
    me_emp = emp("M")
    mg = w.user(me_emp, "manager", a)
    other_mg = emp("M")
    w.user(other_mg, "manager", a)
    out = emp("F")
    w.user(out, "operator", b)
    both = emp("F")
    w.user(both, "operator", None, [a, b])
    mine = emp("F")
    w.user(mine, "auditor", a)
    mes_emp = emp("MES")
    ok(api.post("/api/users", m(emp_no=mes_emp, name="x", role="mes", factory_code=a), w.admin))
    admin_id = sql.scalar("SELECT id FROM sn_user WHERE role = 'admin' ORDER BY id LIMIT 1")
    for e in (other_mg, out, both, mes_emp, me_emp):
        i = _id(sql, e)
        assert api.put(f"/api/users/{i}", m(name="z"), mg).code == "USER_MANAGE_OUT_OF_SCOPE", e
        assert api.post(f"/api/users/{i}/reset-password", m(password="Newp1234"), mg).code == (
            "USER_MANAGE_OUT_OF_SCOPE"
        ), e
        assert api.post(f"/api/users/{i}/enable", {}, mg).code == "USER_MANAGE_OUT_OF_SCOPE", e
    for e in (other_mg, out, both, mes_emp):
        assert api.post(f"/api/users/{_id(sql, e)}/disable", {}, mg).code == "USER_MANAGE_OUT_OF_SCOPE", e
    assert api.post(f"/api/users/{_id(sql, me_emp)}/disable", {}, mg).code == "USER_SELF_DISABLE"
    assert api.put(f"/api/users/{admin_id}", m(name="z"), mg).code == "USER_MANAGE_OUT_OF_SCOPE"
    # MES 接口账户密钥只有系统管理员
    mid = _id(sql, mes_emp)
    assert api.get(f"/api/users/{mid}/secret", mg).code == "USER_MANAGE_FORBIDDEN"
    assert api.post(f"/api/users/{mid}/reset-secret", {}, mg).code == "USER_MANAGE_FORBIDDEN"
    # 列表只看到能管理的账户
    for kind in ("page", "mes", ""):
        rows = ok(api.get(f"/api/users?kind={kind}&page_size=500", mg))["items"]
        seen = {x["emp_no"] for x in rows}
        assert mine in seen or kind == "mes", kind
        assert not seen & {other_mg, out, both, mes_emp, me_emp}, kind
        assert all(x["role"] in ("operator", "auditor") for x in rows), kind
        assert all(set(x["factory_codes"]) <= {a} for x in rows), kind


def test_super_admin_manages_manager(w, api, sql):
    a = w.org(uid("O")[:10])
    e = emp("M")
    w.user(e, "manager", a)
    i = _id(sql, e)
    assert (
        ok(api.put(f"/api/users/{i}", m(name="y", role="operator", factory_codes=[a]), w.admin))["role"] == "operator"
    )
    assert ok(api.put(f"/api/users/{i}", m(name="y", role="manager", factory_codes=[a]), w.admin))["role"] == "manager"
    assert ok(api.post(f"/api/users/{i}/disable", {}, w.admin))["status"] == "DISABLED"
    rows = ok(api.get("/api/users?kind=page&role=manager&page_size=500", w.admin))["items"]
    assert e in {x["emp_no"] for x in rows}


def test_manager_writes_only_authorized_org(w, api):
    """普通操作员的写权限沿用：只能写授权组织，全局规则只能选用；普通操作员本身权限不变。"""
    a, b = w.org(uid("O")[:10]), w.org(uid("O")[:10])
    mg = w.user(emp("M"), "manager", a)
    glob = w.rule(P())

    def new(tok, **kw):
        return api.post(
            "/api/rules",
            m(rule_code=uid("R"), rule_name="r", category_value=uid("PI"), prefix=P(), base=10, seq_len=6) | kw,
            tok,
        )

    created = ok(new(mg))["rule"]
    assert created["factory_code"] == a, "只授权一个组织时默认归属它"
    assert new(mg, factory_code=b).code == "FACTORY_FORBIDDEN"
    body = m(rule_name="x", prefix=P(), base=10, seq_len=6)
    assert api.put(f"/api/rules/{glob}", body, mg).code == "RULE_GLOBAL_READONLY"
    ok(api.put(f"/api/rules/{created['id']}", body, mg))
    op = w.user(emp("F"), "operator", a)
    assert api.get("/api/audits", op).code == "FORBIDDEN", "普通操作员仍没有审计"
    assert api.get("/api/users", op).code == "USER_MANAGE_FORBIDDEN", "普通操作员仍不能管理账户"
