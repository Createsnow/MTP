"""双 SN（PI 级开关）：用户在生成页按整张 PI 判断、手动开启。

- 开启的 PI 每件同时生成第一 SN（号池 sn_item）与第二 SN（sn_item_sub，一件一行，挂在号池记录上）；
  第二 SN 的规则另登记（sn_pi_rule2，配置方式与第一 SN 相同，见 rules 的第二 SN 上下文）。
- 本 PI 下有号（自己生成的、转入的、导入的都算）时开关锁定，开、关都不行（DUAL_LOCKED）；须全部删号、转走后才能改，
  改完重新生成。关闭时同时清除第二 SN 的规则登记（规则库里的规则保留）。
- 分配、推送、拉取、转单、转组织、删号都按件操作，第二 SN 在后台随号走，操作页面只显示第一 SN；
  推送 / 拉取 / 查询接口的明细带 sn2（单 SN 的号为 null）。
"""

from __future__ import annotations

from app.core import utils as util
from app.core.errors import ErrorCode, biz
from app.core.security import CurrentUser
from app.db.session import db, marks
from app.modules.sn.services import rules
from app.platform import audit, locks


def pi_count(pi: str) -> int:
    """本 PI 下的号（按号现在挂的 PI，含转入、导入的）。"""
    return db.count("SELECT COUNT(*) FROM sn_item WHERE pi_no = %s", pi)


def view(pi: str) -> dict:
    """生成页 PI 头部的双 SN 开关：dual 是否开启，locked 有号时锁定（count 为本 PI 下的枚数）。"""
    n = pi_count(pi)
    return {"pi_no": pi, "dual": rules.is_dual(pi), "locked": n > 0, "count": n}


def set_dual(cu: CurrentUser, pi_in: str | None, on: bool) -> dict:
    """开 / 关本 PI 的双 SN：作用于整张 PI，须对本 PI 全部单据的生产组织都有授权；本 PI 下有号时不能改。
    与生成、删号、分配、转移共用 PI 操作锁（期间不会有号写进来）。"""
    pi = util.trim(pi_in)
    if not pi:
        raise biz(ErrorCode.PI_REQUIRED)
    if not db.exists("SELECT EXISTS(SELECT 1 FROM sn_prd_mo WHERE pi = %s)", pi):
        raise biz(ErrorCode.DUAL_PI_NOT_FOUND, pi=pi)
    rules._check_pi_scope(cu, pi)
    with locks.with_pi_lock(pi), db.tx():
        before = db.exists("SELECT EXISTS(SELECT 1 FROM sn_pi_dual WHERE pi_no = %s FOR UPDATE)", pi)
        if before == on:
            return view(pi)
        n = pi_count(pi)
        if n > 0:
            raise biz(ErrorCode.DUAL_LOCKED, pi=pi, count=n)
        if on:
            db.exec("INSERT INTO sn_pi_dual(pi_no, set_by, set_at) VALUES (%s,%s,%s)", pi, cu.emp_no, util.now())
            note = "开启双 SN"
        else:
            reg = db.one("SELECT * FROM sn_pi_rule2 WHERE pi_no = %s FOR UPDATE", pi)
            db.exec("DELETE FROM sn_pi_dual WHERE pi_no = %s", pi)
            db.exec("DELETE FROM sn_pi_rule2 WHERE pi_no = %s", pi)
            note = "关闭双 SN" + ("" if reg is None else f"（清除第二 SN 规则登记 {reg['prefix']}）")
        audit.record(cu, audit.entry(audit.PI_DUAL_SET).pi(pi).info(note))
    return view(pi)


def subs_of(item_ids) -> dict[int, dict]:
    """号池 id → 第二 SN 行（没有的不在结果里）。"""
    ids = sorted({int(i) for i in item_ids if i is not None})
    out: dict[int, dict] = {}
    for k in range(0, len(ids), 2000):
        part = ids[k : k + 2000]
        for r in db.all(f"SELECT * FROM sn_item_sub WHERE item_id IN ({marks(len(part))})", *part):
            out[int(r["item_id"])] = r
    return out


def sn2_of(item_ids) -> dict[int, str]:
    """号池 id → 完整第二 SN。"""
    return {k: v["sn2"] for k, v in subs_of(item_ids).items()}
