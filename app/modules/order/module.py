"""工单模块的装配声明。"""

from __future__ import annotations

from app.api.routes import k3_config, orders
from app.core import scheduler
from app.core.config import settings
from app.platform.module import Job, Module
from app.services import orders as orders_svc


def start_order_sync():
    """定时比对同步：每分钟检查一次是否到期（间隔、暂停在生产订单页「同步设置」改，下一分钟内生效）。"""
    if not settings.get_bool("SN_SYNC_ENABLED", True):
        return None
    return scheduler.start_every(orders_svc.SCHEDULE_TICK_SECONDS, orders_svc.run_scheduled, "sn-order-sync")


MODULE = Module(
    name="order",
    title="工单",
    order=20,
    depends_on=("org",),
    required=True,
    routers=(orders.router, k3_config.router),
    jobs=(Job("order_sync", 30, start_order_sync),),
)
