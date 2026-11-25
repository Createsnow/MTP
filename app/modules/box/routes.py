"""箱号模块的页面接口（/api/box/...）。模块未启用时不挂载。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import UserDep
from app.modules.box import services
from app.modules.box.schemas import SnCheckIn

router = APIRouter(prefix="/box", tags=["box"])


@router.post("/template/sn-check", summary="模板示例：核对 SN 是否存在及当前状态（只读；接入真实功能后删除）")
def sn_check(body: SnCheckIn, cu: UserDep) -> dict:
    return services.check_sns(cu, body.sns)
