"""箱号模块的请求体。"""

from __future__ import annotations

from app.schemas.common import In


class SnCheckIn(In):
    """模板示例：要核对的 SN。"""

    sns: list[str] = []
