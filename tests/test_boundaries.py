"""模块边界（docs/模块化单体改造方案.md 第 6 章）：依赖方向（import-linter）与表归属，本地 pytest 与 CI 都检查。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from devtools import check_table_owners as owners

ROOT = Path(__file__).resolve().parents[1]


def test_table_owners():
    assert owners.check() == []


def test_cross_module_write_is_caught():
    """新模块（如箱号）直接写 SN 的号池：报错；写自己的表不报。"""
    found = owners.writes() | {"app/modules/box/services.py": {"sn_item"}}
    problems = owners.check(found)
    assert len(problems) == 1 and "app/modules/box/services.py（模块 box）写了模块 sn 的表 sn_item" in problems[0]


def test_new_write_in_existing_file_is_caught():
    found = owners.writes()
    found["app/modules/order/services/orders.py"] = found["app/modules/order/services/orders.py"] | {"sn_item"}
    assert any(
        "app/modules/order/services/orders.py（模块 order）写了模块 sn 的表 sn_item" in p for p in owners.check(found)
    )


def test_stale_exception_is_reported():
    found = {k: set(v) for k, v in owners.writes().items()}
    found["app/modules/org/services/factories.py"].discard("sn_push_task")
    assert any(
        "例外 (app/modules/org/services/factories.py, sn_push_task) 已不再出现" in p for p in owners.check(found)
    )


def test_unregistered_table_and_module_prefix():
    table_map = dict(owners.TABLE_OWNER)
    table_map.pop("sn_seq")
    assert any("表 sn_seq 没有登记归属模块" in p for p in owners.check(owners.writes(), table_map))
    table_map = dict(owners.TABLE_OWNER)
    table_map["sn_seq"] = "box"
    assert any("表 sn_seq 归属模块 box，须以 box_ 开头" in p for p in owners.check(owners.writes(), table_map))


def test_import_contracts():
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("未安装 import-linter（uv sync 会装上 dev 依赖）")
    r = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
