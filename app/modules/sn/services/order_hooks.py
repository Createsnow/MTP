"""SN 模块登记到工单扩展点（app/modules/order/extensions.py）的实现，阶段 4b 由工单代码原样搬来。"""

from __future__ import annotations

from app.core.errors import ErrorCode, biz
from app.modules.order import extensions
from app.modules.sn.services import rules


def dual_pi_check(old: str | None, new: str | None) -> None:
    """双 SN 防呆：业务上不会出现单 SN 与双 SN 的 PI 之间改 PI；万一金蝶数据出错，不能确认
    （否则同一张单据里有带第二 SN 和不带第二 SN 的号）。"""
    if old != new and rules.is_dual(old) != rules.is_dual(new):
        raise biz(ErrorCode.ALERT_DUAL_MISMATCH, pi=old, new_pi=new)


def register() -> None:
    extensions.set_line_rules("sn", rules.line_rules_brief)
    extensions.register_pi_change_check("sn.dual", dual_pi_check)
