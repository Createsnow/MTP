"""日志模块的装配声明。"""

from __future__ import annotations

from app.api.routes import audits
from app.platform.module import Module

MODULE = Module(name="log", title="日志", order=40, depends_on=("org",), required=True, routers=(audits.router,))
