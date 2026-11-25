"""路由表不变（模块化阶段 1 起）：模块装配后，每个接口的方法、路径、处理函数和注册顺序都与基准逐条一致。

基准 tests/contract/routes_v1.json 由模块装配前的代码生成（不含随环境变化的 /assets 挂载与 /docs 文档页）。
注册顺序决定同一地址先匹配哪条路由，所以连顺序一起比对。
基准之后新增的接口（新模块的路由、平台的 /api/modules）登记在 ADDED，比对时先去掉；
删改已有接口须先评审，再用下面的命令重新生成基准：

    SN_UPDATE_ROUTES=1 uv run pytest tests/test_routes.py
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from fastapi.routing import _IncludedRouter
from starlette.routing import Mount

GOLDEN = Path(__file__).parent / "contract" / "routes_v1.json"

#: 随运行环境变化、不属于本系统接口的条目，比对时去掉：前端构建产物挂载（有 frontend/dist 才有）、
#: FastAPI 自带的接口文档页（DOCS_ENABLED 控制）
ENV_DEPENDENT = {"/assets", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

#: 基准之后新增的接口：(路径, 处理函数)
ADDED: set[tuple[str, str]] = {("/api/modules", "app.platform.module.list_modules")}


def _name(ep) -> str:
    return f"{ep.__module__}.{ep.__qualname__}"


def _flat(routes: Iterable) -> Iterator[dict]:
    """展开挂载的路由组（FastAPI 0.14x 把 include_router 包成 _IncludedRouter），按匹配顺序列出每条路由。"""
    for r in routes:
        if isinstance(r, _IncludedRouter):
            yield from _flat(r.effective_candidates())
            assert not r.effective_low_priority_routes(), "出现低优先级路由，需补充本测试的展开逻辑"
        elif isinstance(r, Mount):
            yield {"kind": "mount", "path": r.path}
        elif hasattr(r, "original_route"):
            yield {
                "kind": "route",
                "path": r.path,
                "methods": sorted(getattr(r.original_route, "methods", None) or []),
                "endpoint": _name(r.endpoint),
            }
        else:
            yield {"kind": "route", "path": r.path, "methods": sorted(r.methods or []), "endpoint": _name(r.endpoint)}


def routes() -> list[dict]:
    from app.main import create_app

    return [r for r in _flat(create_app().routes) if r["path"] not in ENV_DEPENDENT]


def test_routes_unchanged():
    now = routes()
    if os.environ.get("SN_UPDATE_ROUTES") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    base = json.loads(GOLDEN.read_text(encoding="utf-8"))
    added = [r for r in now if (r["path"], r.get("endpoint")) in ADDED]
    assert len(added) == len(ADDED), f"ADDED 里登记的接口没有注册：{ADDED}"
    kept = [r for r in now if (r["path"], r.get("endpoint")) not in ADDED]
    assert kept == base, "已有接口的方法、路径、处理函数或注册顺序变了（见本文件开头的说明）"
    # 新增接口不能与已有接口同路径（否则改变已有请求的去向）
    assert not {(r["path"], m) for r in added for m in r["methods"]} & {
        (r["path"], m) for r in base if r["kind"] == "route" for m in r["methods"]
    }
