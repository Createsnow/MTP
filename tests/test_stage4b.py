"""阶段 4b 收口后行为不变：每日维护的模块任务（顺序、清理效果）、按天计数、
工单扩展点（订单页 SN 规则列、改 PI 防呆）。"""

from __future__ import annotations

from conftest import ok, uid

from app.platform import partitions


def test_daily_hooks_keep_previous_order(app_client):
    """搬之前 partitions 里的顺序：清理预演与限流计数 → 同步生产组织 → 交付自检 → 单据汇总对账。"""
    assert [h.name for h in partitions._hooks] == [
        "sn_cleanup",
        "factory_sync",
        "delivery_self_check",
        "bill_stat_reconcile",
    ]


def test_daily_cleanup_still_runs(app_client, sql):
    sql.exec("DELETE FROM sn_seq WHERE name = %s", partitions.DAILY_MARK)
    sql.exec(
        "INSERT INTO sn_gen_preview(token, pi_no, qty, base_last_seq, plan_json, created_by, created_at, expires_at) "
        "VALUES ('old4b', 'PI', 1, 0, '{}', 't', UTC_TIMESTAMP() - INTERVAL 8 DAY, UTC_TIMESTAMP()), "
        "('new4b', 'PI', 1, 0, '{}', 't', UTC_TIMESTAMP() - INTERVAL 6 DAY, UTC_TIMESTAMP())"
    )
    sql.exec(
        "INSERT INTO sn_rate_counter(rkey, win, n) VALUES ('old4b', UTC_TIMESTAMP() - INTERVAL 2 DAY, 1), "
        "('new4b', UTC_TIMESTAMP(), 1)"
    )
    partitions.maintain()
    assert [r["token"] for r in sql.all("SELECT token FROM sn_gen_preview WHERE token LIKE '%%4b'")] == ["new4b"]
    assert [r["rkey"] for r in sql.all("SELECT rkey FROM sn_rate_counter WHERE rkey LIKE '%%4b'")] == ["new4b"]
    assert sql.scalar("SELECT val FROM sn_seq WHERE name = %s", partitions.DAILY_MARK) is not None, "做完记完成时间"


def test_count_daily_moved_to_platform(app_client, sql):
    from app.modules.order.services import changes
    from app.platform import metrics

    assert changes.count_daily is metrics.count_daily, "原调用（工单、MES 拉取）不用改"
    before = sql.count("SELECT COALESCE(SUM(n), 0) FROM sn_daily_counter WHERE kind = 'T4B'")
    metrics.count_daily("T4B")
    changes.count_daily("T4B")
    assert sql.count("SELECT COALESCE(SUM(n), 0) FROM sn_daily_counter WHERE kind = 'T4B'") == before + 2


def test_order_lines_rule_column_comes_from_sn(w, api):
    org = w.org(uid("O")[:10])
    pi, bill = uid("PI"), uid("MO")
    w.order(bill, org, "C1", pi, ("M1", 2, "0100"), ("M2", 1, "0100"))
    rid = w.rule("R" + uid("").replace("-", ""), seq_len=6)
    ok(w.bind_pi(pi, rid))
    lines = ok(api.get(f"/api/orders/{bill}/lines", w.admin))["lines"]
    assert [x["rule"]["rule_id"] for x in lines] == [rid, rid], "一个 PI 一个规则，各物料行相同"
    other, obill = uid("PI"), uid("MO")
    w.order(obill, org, "C1", other, ("M1", 1, "0100"))
    assert ok(api.get(f"/api/orders/{obill}/lines", w.admin))["lines"][0]["rule"] is None, (
        "没绑规则的 PI 为空（同搬之前）"
    )


def test_extensions_registered_by_sn(app_client):
    from app.modules.order import extensions

    assert set(extensions._line_rules) == {"sn"}
    assert set(extensions._pi_change_checks) == {"sn.dual"}
    # 不同 PI、都不是双 SN：放行
    extensions.check_pi_change("PI-A-" + uid(""), "PI-B-" + uid(""))
