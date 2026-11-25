"""工单模块的扩展点（阶段 4b）：需要别的模块（SN）补充的信息或检查，由那个模块在装配时登记（Module.subscribe），
工单代码只调这里、不引用 SN（依赖方向 sn → order）。登记按名字覆盖，重复装配（测试里多次 create_app）不会重复。
"""

from __future__ import annotations

from collections.abc import Callable

#: 订单页右表「SN 规则」列：(PI, [(单据编号, 序号)]) → {(单据编号, 序号): 规则摘要}
LineRules = Callable[[str, list[tuple[str, int]]], dict]
#: 确认「改 PI」提醒前的检查：(原 PI, 新 PI)，不允许时抛业务错误（在确认的事务内调用）
PiChangeCheck = Callable[[str | None, str | None], None]

_line_rules: dict[str, LineRules] = {}
_pi_change_checks: dict[str, PiChangeCheck] = {}


def set_line_rules(name: str, fn: LineRules) -> None:
    _line_rules[name] = fn


def line_rules(pi: str, keys: list[tuple[str, int]]) -> dict:
    out: dict = {}
    for fn in _line_rules.values():
        out.update(fn(pi, keys))
    return out


def register_pi_change_check(name: str, fn: PiChangeCheck) -> None:
    _pi_change_checks[name] = fn


def check_pi_change(old: str | None, new: str | None) -> None:
    for fn in _pi_change_checks.values():
        fn(old, new)
