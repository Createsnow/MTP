"""按天计数（sn_daily_counter，底座的表）：各模块记「当天发生了几次某事」，首页与运维核对用。

由工单模块的 changes.count_daily 原样搬来（阶段 4b），changes.count_daily 仍可用（同一个函数）。
"""

from __future__ import annotations

from app.core import utils as util
from app.db.session import db


def count_daily(kind: str, factory: str = "") -> None:
    db.exec(
        "INSERT INTO sn_daily_counter(day, kind, factory_code, n) VALUES (%s,%s,%s,1) "
        "ON DUPLICATE KEY UPDATE n = n + 1",
        util.today(),
        kind,
        factory,
    )
