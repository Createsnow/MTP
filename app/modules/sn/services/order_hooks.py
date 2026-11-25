"""SN 模块登记到工单扩展点（app/modules/order/extensions.py）的实现，阶段 4b、4c 由工单代码原样搬来。"""

from __future__ import annotations

from app.core import utils as util
from app.core.errors import ErrorCode, biz
from app.db.session import db
from app.modules.order import extensions
from app.modules.sn.services import bill_stats, delivery, rules, transfers

#: 未交付、未入批的号：暂停只作用于它们（阶段 4c 由工单 changes 原样搬来）
UNDELIVERED = "(status IN ('PENDING_ALLOC','ALLOCATED') OR (status = 'TO_PUSH' AND delivery_batch_no IS NULL))"


def apply_item_suspend(bill_no: str, reason: str | None) -> int:
    """把该单未交付、未入批的号的暂停标记改为 reason；已交付或已入批的号不暂停。返回改动的枚数。
    阶段 4c 由工单 changes._set_items_suspend 原样搬来（语句、顺序相同，在调用方的事务内执行）。"""
    total = db.exec(
        f"UPDATE sn_item SET suspend = %s, updated_at = %s WHERE bill_no = %s AND {UNDELIVERED} "
        "AND NOT (suspend <=> %s)",
        reason,
        util.now(),
        bill_no,
        reason,
    )
    # 已交付或已入批的号不暂停
    cleared = db.exec(
        f"UPDATE sn_item SET suspend = NULL WHERE bill_no = %s AND suspend IS NOT NULL AND NOT {UNDELIVERED}",
        bill_no,
    )
    if total or cleared:
        bill_stats.touch_bill(bill_no)
    return total


def dual_pi_check(old: str | None, new: str | None) -> None:
    """双 SN 防呆：业务上不会出现单 SN 与双 SN 的 PI 之间改 PI；万一金蝶数据出错，不能确认
    （否则同一张单据里有带第二 SN 和不带第二 SN 的号）。"""
    if old != new and rules.is_dual(old) != rules.is_dual(new):
        raise biz(ErrorCode.ALERT_DUAL_MISMATCH, pi=old, new_pi=new)


def register() -> None:
    extensions.set_line_rules("sn", rules.line_rules_brief)
    extensions.register_pi_change_check("sn.dual", dual_pi_check)
    extensions.register("apply_item_suspend", apply_item_suspend)
    extensions.register("kick_bill", delivery.kick_bill)
    extensions.register("map_material", transfers.map_material)
