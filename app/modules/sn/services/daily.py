"""SN 模块的每日任务（由底座每日维护 app/platform/partitions.py 调用，序号见 ../module.py）。

阶段 4b 由底座原样搬来：语句、日志事件名、失败处理都与搬之前相同。
"""

from __future__ import annotations

import structlog

from app.db.session import db
from app.modules.sn.services import bill_stats, delivery

#: 日志名沿用搬之前的底座模块，运维按 logger 过滤不受影响
log = structlog.get_logger("app.platform.partitions")


def cleanup() -> None:
    """清理过期的生成预演（7 天）与 MES 拉取限流计数（1 天）。失败即中止本次每日维护（下次补做），与搬之前相同。"""
    db.exec("DELETE FROM sn_gen_preview WHERE created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY)")
    db.exec("DELETE FROM sn_rate_counter WHERE win < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)")


def self_check() -> None:
    """每日交付自检（5.2.5 第 5 条）；失败只记日志。"""
    try:
        delivery.self_check()
    except Exception:  # noqa: BLE001
        log.exception("daily_self_check_failed")


def reconcile() -> None:
    """单据汇总对账；失败只记日志。"""
    try:
        bad = bill_stats.reconcile()
        log.info("bill_stat_reconciled", mismatched=bad)
    except Exception:  # noqa: BLE001
        log.exception("bill_stat_reconcile_failed")
