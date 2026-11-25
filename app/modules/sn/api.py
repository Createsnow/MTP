"""SN 模块对外接口（docs/模块化单体改造方案.md 第 7.1 节）：其他模块（箱号、托号等）只能调这里，不能引用 SN 的
services / routes，也不能直接写 SN 的表（import-linter 契约 box-pallet、devtools/check_table_owners.py）。

约定：
- 只放其他模块实际要用的能力；签名或语义变更须同步改调用方，并补 tests/test_sn_api.py。
- 参数与返回只用普通类型和 frozen dataclass，不暴露表结构；状态值见下面的常量。
- 现在只有只读能力。号池 sn_item 是 MES 推送 / 拉取报文的来源（MES 接口已联调冻结），装箱关系等新数据记在调用方
  自己的表里（如 box_carton_item），不写回号池；确需写入时先评审，并保证 MES 契约测试不变。
- 读范围按账户的授权生产组织（与 SN 查询页一致）：系统管理员全部；其他账户只看授权组织的号（已分配的按号上的组织，
  待分配的按所在单据的组织），范围外的号与不存在的号一样不返回。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.core import utils as util
from app.core.errors import ErrorCode, biz
from app.core.security import CurrentUser
from app.db.session import db, marks
from app.modules.org.services import factories

# ---------------------------------------------------------------- 号的状态（sn_item.status）

#: 已生成、待分配
PENDING_ALLOC = util.PENDING_ALLOC
#: 已分配到未对接 MES 的生产组织（页面下载）
ALLOCATED = util.ALLOCATED
#: 已分配、待推送给 MES（含 MES 拉取前）
TO_PUSH = util.TO_PUSH
#: 已交付给 MES（推送或拉取，回执确认）
PUSHED = util.PUSHED

#: 一次最多查询的 SN 数
MAX_SNS = 5000


@dataclass(frozen=True)
class SnInfo:
    sn: str
    #: 第二 SN（双 SN 的 PI 才有）
    sn2: str | None
    status: str
    #: 暂停交付：REVERSAL 单据反审核中 / CHANGE 订单变更待确认；None = 未暂停
    suspend: str | None
    pi: str
    bill_no: str
    bill_seq: int
    material_code: str
    customer_code: str
    #: 生产组织（分配后才有）
    factory_code: str | None


def sn_info(cu: CurrentUser, sns: Iterable[str]) -> dict[str, SnInfo]:
    """按完整 SN 查号的当前信息（只读）。返回 SN → 信息；不存在或不在读范围内的不返回。

    SN 区分大小写、按原样匹配（前后空白去掉，空值忽略）；一次最多 MAX_SNS 枚，超出报参数不合法。
    """
    keys = list(dict.fromkeys(s.strip() for s in sns if s and s.strip()))
    if not keys:
        return {}
    if len(keys) > MAX_SNS:
        raise biz(ErrorCode.VALIDATION, detail="sns")
    scope, sargs = factories.item_scope_sql(cu.read_factories(None), "i.")
    rows = db.all(
        "SELECT i.sn, s.sn2, i.status, i.suspend, i.pi_no, i.bill_no, i.bill_seq, i.material_code, "
        "i.customer_code, i.factory_code FROM sn_item i LEFT JOIN sn_item_sub s ON s.item_id = i.id "
        f"WHERE i.sn IN ({marks(len(keys))}){scope}",
        *keys,
        *sargs,
    )
    return {
        r["sn"]: SnInfo(
            sn=r["sn"],
            sn2=r["sn2"],
            status=r["status"],
            suspend=r["suspend"],
            pi=r["pi_no"],
            bill_no=r["bill_no"],
            bill_seq=int(r["bill_seq"]),
            material_code=r["material_code"],
            customer_code=r["customer_code"],
            factory_code=r["factory_code"],
        )
        for r in rows
    }
