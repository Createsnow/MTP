"""箱号模块的业务逻辑。读 SN 只调 app.modules.sn.api；箱号自己的数据写在 box_ 表（需求定了再建）。"""

from __future__ import annotations

from app.core.security import CurrentUser
from app.modules.sn import api as sn_api


def check_sns(cu: CurrentUser, sns: list[str]) -> dict:
    """模板示例：逐枚给出 SN 是否存在（在读范围内）及其当前状态、单据、生产组织。真实的装箱规则待需求确定。"""
    info = sn_api.sn_info(cu, sns)
    items = []
    for sn in dict.fromkeys(s.strip() for s in sns if s and s.strip()):
        x = info.get(sn)
        items.append(
            {
                "sn": sn,
                "found": x is not None,
                "status": None if x is None else x.status,
                "pi": None if x is None else x.pi,
                "bill_no": None if x is None else x.bill_no,
                "factory_code": None if x is None else x.factory_code,
            }
        )
    return {"items": items}
