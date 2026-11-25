"""箱号模块的装配声明（模板）：默认不启用；测试环境 SN_MODULES=org,order,sn,log,dashboard,box 打开。"""

from __future__ import annotations

from app.modules.box import routes
from app.platform.module import Module

MODULE = Module(
    name="box",
    title="箱号管理",
    order=60,
    depends_on=("org", "order", "sn"),
    default_on=False,
    routers=(routes.router,),
)
