"""托号模块的装配声明（模板）：默认不启用；依赖箱号，测试环境 SN_MODULES=org,order,sn,log,dashboard,box,pallet 打开。"""

from __future__ import annotations

from app.modules.pallet import routes
from app.platform.module import Module

MODULE = Module(
    name="pallet",
    title="托号管理",
    order=70,
    depends_on=("org", "order", "sn", "box"),
    default_on=False,
    routers=(routes.router,),
)
