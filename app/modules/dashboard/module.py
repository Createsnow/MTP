"""首页看板的装配声明。"""

from __future__ import annotations

from app.modules.dashboard.routes import dashboard
from app.platform.module import Module

MODULE = Module(
    name="dashboard",
    title="首页看板",
    order=50,
    depends_on=("org", "order", "sn"),
    required=True,
    routers=(dashboard.router,),
)
