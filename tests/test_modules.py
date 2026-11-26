"""模块装配（docs/模块化单体改造方案.md 第 8 章、阶段 1）：启动步骤与后台任务的顺序与模块化之前一致；
SN_MODULES 检查。"""

from __future__ import annotations

import pytest
from fastapi import APIRouter

from app.platform.module import Module, discover, enabled

#: 模块化之前 main.startup 里的顺序（建表 → 单据统计补齐 → 回收中断的生成任务 → 建管理员 → 维护分区 → 演示数据）
#: sync_start_date（2026-11-26 新增）：建库后首次启动按 .env 的 SN_SYNC_START_MONTHS 写入同步起始日期
BEFORE_STEPS = ["init_db", "bill_stats_fill", "gen_recover", "init_admin", "sync_start_date", "maintain", "demo_seed"]
#: 模块化之前 main.lifespan 里的启动顺序（每日维护 → 回收生成任务 → 比对同步 → 推送调度 → 单据统计）
BEFORE_JOBS = ["daily", "gen_recover", "order_sync", "push", "bill_stats"]


def test_modules_discovered_in_order():
    assert list(discover()) == ["org", "order", "sn", "log", "dashboard", "box", "pallet"]


def test_startup_steps_and_jobs_keep_previous_order():
    from app.main import jobs, startup_steps

    mods = enabled(discover(), None)
    assert [s.name for s in startup_steps(mods)] == BEFORE_STEPS
    assert [j.name for j in jobs(mods)] == BEFORE_JOBS


def test_default_enables_all_existing_modules():
    assert [m.name for m in enabled(discover(), None)] == ["org", "order", "sn", "log", "dashboard"]
    assert [m.name for m in enabled(discover(), " ")] == ["org", "order", "sn", "log", "dashboard"]
    # 写的顺序不影响装配顺序
    got = enabled(discover(), "dashboard,log,sn,order,org")
    assert [m.name for m in got] == ["org", "order", "sn", "log", "dashboard"]


def test_required_and_unknown_modules():
    with pytest.raises(RuntimeError, match="不认识的模块：crate"):
        enabled(discover(), "org,order,sn,log,dashboard,crate")
    with pytest.raises(RuntimeError, match="模块 sn（SN 管理）必须启用"):
        enabled(discover(), "org,order,log,dashboard")


def _fake() -> dict[str, Module]:
    r = APIRouter()
    return {
        "core": Module(name="core", title="核心", order=10, required=True, routers=(r,)),
        "box": Module(name="box", title="箱号", order=60, depends_on=("core",), default_on=False),
        "pallet": Module(name="pallet", title="托号", order=70, depends_on=("box",), default_on=False),
    }


def test_new_module_off_by_default_and_dependencies_checked():
    assert [m.name for m in enabled(_fake(), None)] == ["core"], "新模块默认不启用"
    assert [m.name for m in enabled(_fake(), "core,box")] == ["core", "box"]
    with pytest.raises(RuntimeError, match="模块 pallet（托号）依赖 box"):
        enabled(_fake(), "core,pallet")
    bad = _fake() | {"box": Module(name="box", title="箱号", order=5, depends_on=("core",))}
    with pytest.raises(RuntimeError, match="core 的 order 须小于 box"):
        enabled(bad, "core,box")


def test_modules_api(api, admin):
    assert api.get("/api/modules", admin).body == {"modules": ["org", "order", "sn", "log", "dashboard"]}
    assert api.get("/api/modules", None).status == 401


def test_box_and_pallet_templates_off_by_default():
    """箱号、托号模板默认不启用（正式环境不受影响）；托号依赖箱号。"""
    assert [m.name for m in enabled(discover(), None)] == ["org", "order", "sn", "log", "dashboard"]
    both = enabled(discover(), "org,order,sn,log,dashboard,box,pallet")
    assert [m.name for m in both] == ["org", "order", "sn", "log", "dashboard", "box", "pallet"]
    with pytest.raises(RuntimeError, match="模块 pallet（托号管理）依赖 box"):
        enabled(discover(), "org,order,sn,log,dashboard,pallet")


def test_template_routes_mounted_only_when_enabled(monkeypatch):
    from test_routes import routes

    paths = {r["path"] for r in routes()}
    assert not {p for p in paths if p.startswith(("/api/box", "/api/pallet"))}, "默认不挂载"
    monkeypatch.setenv("SN_MODULES", "org,order,sn,log,dashboard,box,pallet")
    paths = {r["path"] for r in routes()}
    assert {"/api/box/template/sn-check", "/api/pallet/template/sn-check"} <= paths
