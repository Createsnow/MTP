"""组织模块的扩展点（阶段 4c）：组织的动作需要别的模块（SN）跟着处理时，由那个模块在装配时登记
（Module.subscribe），组织代码只调这里、不引用 SN（依赖方向 sn → org）。在调用方的线程与事务内同步执行，
与直接调用相同；没登记就报错（RuntimeError），不会悄悄跳过。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

#: 生产组织开 / 关「对接 MES」后号池的处理：(组织编码, 是否开启, 时间) → 改动的号数
MesToggled = Callable[[str, bool, datetime], int]

_on_mes_toggle: dict[str, MesToggled] = {}


def register_mes_toggle(name: str, fn: MesToggled) -> None:
    _on_mes_toggle[name] = fn


def mes_toggled(factory_code: str, enabled: bool, now: datetime) -> int:
    if not _on_mes_toggle:
        raise RuntimeError("组织扩展点 mes_toggle 未登记（SN 模块未装配）")
    return sum(fn(factory_code, enabled, now) for fn in _on_mes_toggle.values())
