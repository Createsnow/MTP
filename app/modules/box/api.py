"""箱号模块对外接口：托号等其他模块只能调这里（import-linter 契约 box-pallet）。

需求未定，现在没有对外能力。以后按托号实际要用的添加（例如按箱号查箱内 SN、箱的状态），参数与返回只用普通类型和
frozen dataclass，并补契约测试 tests/test_box_api.py。
"""

from __future__ import annotations

__all__: list[str] = []
