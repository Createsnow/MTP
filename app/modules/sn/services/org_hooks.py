"""SN 模块登记到组织扩展点（app/modules/org/extensions.py）的实现，阶段 4c 由组织代码原样搬来。"""

from __future__ import annotations

from datetime import datetime

from app.core import utils as util
from app.db.session import db
from app.modules.org import extensions
from app.modules.sn.services import bill_stats


def mes_toggled(fc: str, enabled: bool, now: datetime) -> int:
    """生产组织开 / 关「对接 MES」后号池的处理（在组织的事务内，检查都已由组织做过），返回改动的号数。

    开启：已分配 → 待推送（未入批）；不自动推送，由总部「继续推送」或等 MES 拉取。
    关闭：待推送（未入批）→ 已分配；已推送的号不变；删掉已停止 / 已完成的推送任务。
    """
    if enabled:
        n = db.exec(
            "UPDATE sn_item SET status = 'TO_PUSH', push_type = 'NEW', updated_at = %s "
            "WHERE status = 'ALLOCATED' AND factory_code = %s",
            now,
            fc,
        )
        if n:
            bill_stats.touch_factory(fc, util.TO_PUSH)
        return n
    n = db.exec(
        "UPDATE sn_item SET status = 'ALLOCATED', updated_at = %s "
        "WHERE status = 'TO_PUSH' AND factory_code = %s AND delivery_batch_no IS NULL",
        now,
        fc,
    )
    if n:
        bill_stats.touch_factory(fc, util.ALLOCATED)
    db.exec("DELETE FROM sn_push_task WHERE factory_code = %s AND state IN ('STOPPED','DONE')", fc)
    return n


def register() -> None:
    extensions.register_mes_toggle("sn", mes_toggled)
