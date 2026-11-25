"""SN 管理模块的装配声明。MES 接口（routes/mes.py）作为对外接口挂在全部页面接口之后，与模块化之前的顺序一致。"""

from __future__ import annotations

from app.core import scheduler
from app.modules.sn.routes import allocation, delivery, generate, mes, rules, sn, sn_export, sn_import, transfers
from app.modules.sn.services import bill_stats, daily, order_hooks
from app.modules.sn.services import delivery as delivery_svc
from app.modules.sn.services import generate as generate_svc
from app.platform.module import Job, Module, Step


def start_gen_recover():
    """别的实例被杀留下的 RUNNING 生成任务：每分钟检查一次（拿得到 PI 操作锁才回收，不会碰正在执行的任务）。"""
    return scheduler.start_every(
        generate_svc.RECOVER_INTERVAL_SECONDS, generate_svc.recover_interrupted, "sn-gen-recover"
    )


MODULE = Module(
    name="sn",
    title="SN 管理",
    order=30,
    depends_on=("org", "order"),
    required=True,
    routers=(
        rules.router,
        generate.router,
        allocation.router,
        delivery.router,
        sn_export.router,
        sn_import.router,
        transfers.router,
        sn.router,
    ),
    open_routers=(mes.router,),
    subscribe=order_hooks.register,
    startup=(
        Step("bill_stats_fill", 20, bill_stats.ensure_filled),
        Step("gen_recover", 30, generate_svc.recover_interrupted),
    ),
    daily=(
        Step("sn_cleanup", 10, daily.cleanup),
        Step("delivery_self_check", 30, daily.self_check),
        Step("bill_stat_reconcile", 40, daily.reconcile),
    ),
    jobs=(
        Job("gen_recover", 20, start_gen_recover),
        Job("push", 40, delivery_svc.start_scheduler),
        Job("bill_stats", 50, bill_stats.start_scheduler),
    ),
)
