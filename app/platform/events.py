"""应用内事件（docs/模块化单体改造方案.md 第 7.3 节）：下游模块需要通知上游时用它，不反向 import。

- 同步分发，在发布方的线程与数据库事务内执行；订阅方抛错则异常传回发布方（整个事务回滚）。不是消息队列：不异步、
  不落库、不重试。
- 事件类型由发布方在自己的 events.py 定义（frozen dataclass），订阅方 import 发布方的事件类型（方向正确）。
- 订阅在模块的 Module.subscribe 里登记，应用创建时（create_app）统一执行；reset() 先清空，避免重复登记。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

_subs: dict[type, list[Callable[[object], None]]] = defaultdict(list)


def subscribe(event_type: type, fn: Callable[[object], None]) -> None:
    _subs[event_type].append(fn)


def publish(event: object) -> None:
    for fn in list(_subs.get(type(event), ())):
        fn(event)


def reset() -> None:
    _subs.clear()
