"""组织模块的每日任务（由底座每日维护 app/platform/partitions.py 调用，序号见 ../module.py）。

阶段 4b 由底座原样搬来：调用、日志事件名、失败处理都与搬之前相同。
"""

from __future__ import annotations

import structlog

from app.core.security import CurrentUser
from app.modules.org.services import factories

#: 日志名沿用搬之前的底座模块，运维按 logger 过滤不受影响
log = structlog.get_logger("app.platform.partitions")


def sync_factories() -> None:
    """从金蝶同步生产组织（F-03）；失败只记日志。"""
    try:
        factories.sync_from_k3(CurrentUser.system())
    except Exception as e:  # noqa: BLE001
        log.warning("daily_factory_sync_failed", err=str(e))
