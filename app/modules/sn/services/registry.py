"""全公司 SN 唯一登记（sn_registry，2026-11-26 防重加固）。

第一 SN（号池 sn_item，含 SN 导入的号）与第二 SN（sn_item_sub）各有自己的唯一键，但两张表之间互相管不到：
原来靠前缀用途分开、前缀不能互为开头来保证第一 SN 与第二 SN 不相同，再加生成 / 导入时的查重。这张表把两边的
完整 SN 放在同一个主键下，任何两处要存同一个 SN 时由数据库直接拦下、整笔回滚，与加锁时机、前缀判断无关。

- 写号（生成第一 SN、第二 SN，SN 导入）时在同一事务里登记；删号、导入整单删除时在同一事务里移除；
  转单、转组织不改 SN 文本，不动登记。
- 只做兜底：业务上原本允许的操作照常成功（第一 SN 与第二 SN 本来就不会相同）；原来就会报撞号的照原样报，
  报错码不变（生成 GEN_DUPLICATE_SN / GEN_IMPORTED_SN，导入 IMPORT_CONFLICT）。
- 用显式的值插入（不用 INSERT … SELECT）：INSERT … SELECT 会给号池的索引区间加共享间隙锁，生成事务长，
  会挡住别的前缀的生成。
"""

from __future__ import annotations

from app.db.session import db, marks

FIRST = 1
SECOND = 2

_INSERT = "INSERT INTO sn_registry(sn, kind) VALUES (%s, %s)"


def add(sns: list[str], kind: int) -> None:
    """登记一批完整 SN（当前事务内）；与已登记的相同时抛 IntegrityError（重复键），由调用方按原来的撞号处理。"""
    if sns:
        db.exec_many(_INSERT, [(sn, kind) for sn in sns])


def remove(sns: list[str], kind: int) -> None:
    """移除一批完整 SN 的登记（当前事务内，按主键删，只删这一种的）。"""
    for i in range(0, len(sns), 2000):
        part = sns[i : i + 2000]
        db.exec(f"DELETE FROM sn_registry WHERE sn IN ({marks(len(part))}) AND kind = %s", *part, kind)


def remove_items(ids: list[int]) -> None:
    """号池记录（及其第二 SN）删除前移除它们的登记（当前事务内）。"""
    for i in range(0, len(ids), 2000):
        part = ids[i : i + 2000]
        remove(db.column(f"SELECT sn FROM sn_item WHERE id IN ({marks(len(part))})", *part), FIRST)
        remove(db.column(f"SELECT sn2 FROM sn_item_sub WHERE item_id IN ({marks(len(part))})", *part), SECOND)


def holder(sns: list[str]) -> dict | None:
    """登记里已有的、与 sns 相同的一个号：{sn, kind, pi_no, origin}（第二 SN 的 origin 为 GEN）；没有为 None。
    加锁读（读最新已提交的），撞号报错时用，之后整笔回滚。"""
    if not sns:
        return None
    r = db.one(
        f"SELECT sn, kind FROM sn_registry WHERE sn IN ({marks(len(sns))}) ORDER BY kind LIMIT 1 LOCK IN SHARE MODE",
        *sns,
    )
    if r is None:
        return None
    if int(r["kind"]) == SECOND:
        x = db.one("SELECT seq_pi_no pi_no FROM sn_item_sub WHERE sn2 = %s LOCK IN SHARE MODE", r["sn"])
        return {"sn": r["sn"], "kind": SECOND, "pi_no": x["pi_no"] if x else "-", "origin": "GEN"}
    x = db.one("SELECT pi_no, origin FROM sn_item WHERE sn = %s LOCK IN SHARE MODE", r["sn"])
    return {
        "sn": r["sn"],
        "kind": FIRST,
        "pi_no": x["pi_no"] if x else "-",
        "origin": x["origin"] if x else "GEN",
    }
