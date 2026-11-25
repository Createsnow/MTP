"""工单模块的扩展点（阶段 4b、4c）：需要别的模块（SN）补充的信息、检查或动作，由那个模块在装配时登记
（Module.subscribe），工单代码只调这里、不引用 SN（依赖方向 sn → order）。登记按名字覆盖，重复装配（测试里多次
create_app）不会重复。都在调用方的线程与事务内同步执行，与直接调用相同。

- 可选扩展点（订单页 SN 规则列、改 PI 检查）：没人登记时为空 / 放行。
- 必需扩展点（号的暂停标记、解除暂停后启动推送、确认物料对应）：没登记就报错（RuntimeError），不会悄悄跳过。
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


# ---------------------------------------------------------------- 必需扩展点（SN 模块登记）

#: 把该单未交付、未入批的号的暂停标记改为 reason（None = 解除），返回改动的枚数
ApplyItemSuspend = Callable[[str, str | None], int]
#: 解除暂停后启动该单据的推送（事务提交后调用）
KickBill = Callable[[str], None]
#: 确认物料对应：(当前账户, 提醒行, 目标物料行序号, 说明)，在自己的事务内转移并关闭提醒
MapMaterial = Callable[[object, dict, int | None, str | None], None]

_required: dict[str, Callable] = {}


def register(name: str, fn: Callable) -> None:
    """登记必需扩展点：apply_item_suspend、kick_bill、map_material。"""
    if name not in ("apply_item_suspend", "kick_bill", "map_material"):
        raise RuntimeError(f"工单模块没有扩展点 {name}")
    _required[name] = fn


def _get(name: str) -> Callable:
    fn = _required.get(name)
    if fn is None:
        raise RuntimeError(f"工单扩展点 {name} 未登记（SN 模块未装配）")
    return fn


def apply_item_suspend(bill_no: str, reason: str | None) -> int:
    return _get("apply_item_suspend")(bill_no, reason)


def kick_bill(bill_no: str) -> None:
    _get("kick_bill")(bill_no)


def map_material(cu, alert: dict, target_seq: int | None, note: str | None) -> None:
    _get("map_material")(cu, alert, target_seq, note)
