"""托号模块的业务逻辑。读箱号只调 app.modules.box.api，读 SN 只调 app.modules.sn.api；
数据写在 plt_ 表（需求定了再建）。"""

from __future__ import annotations

from app.core.security import CurrentUser
from app.modules.sn import api as sn_api


def check_sns(cu: CurrentUser, sns: list[str]) -> dict:
    """模板示例：给出在读范围内、存在的 SN 数与不存在的 SN。真实的装托规则待需求确定。"""
    keys = list(dict.fromkeys(s.strip() for s in sns if s and s.strip()))
    info = sn_api.sn_info(cu, keys)
    return {"found": len(info), "missing": [s for s in keys if s not in info]}
