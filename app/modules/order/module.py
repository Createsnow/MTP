"""工单模块的装配声明。"""

from __future__ import annotations

from app.core import scheduler
from app.core.config import settings
from app.modules.order.routes import k3_config, orders
from app.modules.order.services import orders as orders_svc
from app.platform.module import Job, Module, Step


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
    # 建库后首次启动：同步起始日期 = 今天往前 .env 的 SN_SYNC_START_MONTHS 个月（在后台同步启动之前写入）
    startup=(Step("sync_start_date", 45, orders_svc.init_start_date),),
    jobs=(Job("order_sync", 30, start_order_sync),),
)
