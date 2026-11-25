"""模块描述与装配（docs/模块化单体改造方案.md 第 8 章）。

每个业务模块在 app/modules/<模块>/module.py 里声明一个 MODULE：自己的路由、启动步骤、后台任务、依赖。
main.py 只做装配：discover() 找出全部模块 → enabled() 按 SN_MODULES 选出启用的并检查 → 依次挂路由、执行启动步骤、
启动后台任务。新增模块只新建 app/modules/<模块>/，不改 main.py、不改路由清单。

- 挂路由的顺序：按 Module.order 依次挂各模块的 routers，再依次挂各模块的 open_routers（MES 等对外接口），
  与模块化之前的注册顺序一致（tests/test_routes.py 逐条比对）。
- 启动步骤、后台任务都带全局序号 order：底座与各模块的步骤合在一起按序号执行，与模块化之前的顺序一致
  （tests/test_modules.py 校验）。序号不能重复。
- SN_MODULES：启用的模块，逗号分隔；不设时启用全部 default_on 的模块。required 的模块必须启用（现有模块之间的代码
  还没有解耦，见方案阶段 4），依赖的模块必须启用，不认识的模块名直接报错，都在启动时拒绝启动。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Request

from app.api.deps import UserDep
from app.core.config import settings


class Stoppable(Protocol):
    def stop(self) -> None: ...


@dataclass(frozen=True)
class Step:
    """启动步骤：在启动初始化锁内、建表之后按 order 执行（见 main.startup）。"""

    name: str
    order: int
    run: Callable[[], None]


@dataclass(frozen=True)
class Job:
    """后台任务：start() 启动并返回可停止的对象；不启动（如被配置关闭）时返回 None。按 order 启动、倒序停止。"""

    name: str
    order: int
    start: Callable[[], Stoppable | None]


@dataclass(frozen=True)
class Module:
    name: str
    #: 中文名（日志与报错用）
    title: str
    #: 装配顺序：挂路由按它排；依赖的模块须排在前面
    order: int
    depends_on: tuple[str, ...] = ()
    #: 必须启用：现有 org / order / sn 等模块之间仍有直接调用，不能单独关闭
    required: bool = False
    #: 不设 SN_MODULES 时是否启用；新模块（箱号、托号）开发期间为 False，测试环境用 SN_MODULES 打开
    default_on: bool = True
    #: 页面接口（挂在 /api 下）
    routers: tuple[APIRouter, ...] = ()
    #: 对外接口（MES 等，挂在全部模块的页面接口之后）
    open_routers: tuple[APIRouter, ...] = ()
    startup: tuple[Step, ...] = ()
    jobs: tuple[Job, ...] = ()
    #: 每日维护里执行的任务（app/platform/partitions.py；序号与其他模块的统一排序，在归档之后执行）
    daily: tuple[Step, ...] = ()
    #: 登记应用内事件的订阅与扩展点（app.platform.events.subscribe 等）
    subscribe: Callable[[], None] | None = None


#: 业务模块所在的包：按约定在运行时查找（底座不静态引用业务模块，import-linter 契约 platform）
MODULES_PACKAGE = "app.modules"


def discover() -> dict[str, Module]:
    """app/modules/ 下的全部模块（每个子包的 module.py 里的 MODULE），按 order 排序。"""
    pkg = importlib.import_module(MODULES_PACKAGE)
    found: dict[str, Module] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.ispkg:
            continue
        mod = importlib.import_module(f"{MODULES_PACKAGE}.{info.name}.module").MODULE
        if mod.name != info.name:
            raise RuntimeError(f"模块目录 {info.name} 里的 MODULE.name 是 {mod.name}，须与目录名一致")
        found[mod.name] = mod
    return dict(sorted(found.items(), key=lambda kv: kv[1].order))


def enabled(available: dict[str, Module], configured: str | None) -> list[Module]:
    """按 SN_MODULES 选出启用的模块并检查；configured 为空时启用全部 default_on 的模块。"""
    if configured is None or not configured.strip():
        names = [n for n, m in available.items() if m.default_on]
    else:
        names = [x.strip() for x in configured.split(",") if x.strip()]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise RuntimeError(f"SN_MODULES 里有不认识的模块：{'、'.join(unknown)}（可用：{'、'.join(available)}）")
    chosen = [m for n, m in available.items() if n in names]
    on = {m.name for m in chosen}
    for m in available.values():
        if m.required and m.name not in on:
            raise RuntimeError(f"模块 {m.name}（{m.title}）必须启用，SN_MODULES 里不能去掉")
    for m in chosen:
        for d in m.depends_on:
            if d not in on:
                raise RuntimeError(f"模块 {m.name}（{m.title}）依赖 {d}，须在 SN_MODULES 里同时启用")
            if available[d].order >= m.order:
                raise RuntimeError(f"模块 {m.name} 依赖 {d}，{d} 的 order 须小于 {m.name}")
    return chosen


def load() -> list[Module]:
    return enabled(discover(), settings.raw("SN_MODULES"))


def check_unique(items: list[Step] | list[Job], what: str) -> None:
    seen: dict[int, str] = {}
    for x in items:
        if x.order in seen:
            raise RuntimeError(f"{what} {x.name} 与 {seen[x.order]} 的序号 {x.order} 重复")
        seen[x.order] = x.name


def api_router(mods: list[Module]) -> APIRouter:
    """/api 下的全部接口：各模块的页面接口 → 各模块的对外接口 → 平台接口（/api/modules）。"""
    r = APIRouter()
    for m in mods:
        for x in m.routers:
            r.include_router(x)
    for m in mods:
        for x in m.open_routers:
            r.include_router(x)
    r.include_router(router)
    return r


# ---------------------------------------------------------------- 平台接口

router = APIRouter(tags=["platform"])


@router.get("/modules", summary="启用的业务模块（前端据此显示菜单；任何已登录账户）")
def list_modules(request: Request, cu: UserDep) -> dict:
    return {"modules": [m.name for m in request.app.state.modules]}
