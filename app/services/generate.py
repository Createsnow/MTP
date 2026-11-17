"""SN 生成（F-09）。删号见 deletion，分配见 allocation。

以规则前缀为排号基准：一个 PI 一个规则前缀（rules.pi_rule 登记）；同一前缀下各 PI、各单据共用一套十进制流水，
每次生成都接在前缀最大号（sn_prefix_counter，只增不减）之后。号上记 seq_prefix，(seq_prefix, seq_dec) 唯一。

生成：在一张 PI 下整单（勾选单据）或按物料行（点选）生成，须先勾选；最小单位是物料行——范围内每个还有额度的物料行
一次生成它的全部额度，不拆成两次。排号顺序（_gen_order）：先按附件项次从小到大（按数字比）；同项次时，
点选物料行按点击先后，勾整张单据按单据勾选先后、同一单据内按行号。

项次拦截（_check_order，按前缀）：一行的项次在这个前缀下已开始（有现存的号）时不拦，接在最大号之后生成；
还没开始时，登记了这个前缀的 PI 里只要还有更小项次的行有额度，就拦住（ATTACH_ORDER_BLOCKED，列出挡路的行）。
项次为空或不是数字的行自己不能生成，并挡住这个前缀全部没开始的项次。失效的物料行、冻结或暂停交付单据的行、
删过号的行不挡。同一次生成里的行彼此不挡（小项次排在前）。

流水来源（预演时选）：
- MAX 从前缀最大号 + 1 起往后编（默认）；起始号（PI 登记的）大于下一个号时从起始号起，跳过的流水成为空号段；
- RANGE 手动填起止流水号（十进制），在前缀的空号段（删号腾出、起始号跳过的流水，同前缀各 PI 都能用）或最大号之后
  取：范围内空闲的流水从小到大使用，再用所选空号段，不够时其余从 max(最大号, 止号) + 1 起接着编。

必须先预演（只给出起止号、各物料行数量和号段，不落库），预演只对发起人有效、默认 30 分钟、只能生成一次。
确认生成时在前缀锁内按最新数据重算：物料行、数量、规则不变而只是同前缀别的 PI 先生成了（最大号变大）时，
自动顺延到新的最大号之后（任务计划 shifted）；手动起止号内的号被占用、项次拦截、规则或额度变了时预演失效。
一次生成在一个事务里完成（共用一个生成批次），撞号整次回滚（含与 SN 导入登记的号相同，见 sn_import）；
不超过 SN_GEN_SYNC_THRESHOLD 当场完成，更多转后台任务（进度、可取消）；服务重启时没跑完的任务视为失败。
生成（含取消）、删号与分配共用 PI 操作锁；同一前缀的生成另由前缀锁串行。

并发：一个实例同时写号池的生成不超过 SN_GEN_CONCURRENCY（默认 4，当场与后台合计），其余在任务里排队
（任务照常是生成中，PI 操作锁照常占着）；多张 PI 同时写号池时撞上死锁（MySQL 1213，整笔事务已回滚）自动整笔重做，
最多 DEADLOCK_ATTEMPTS 次，仍死锁才记为失败。
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import pymysql
import structlog

from app.core import encoding as codec
from app.core import utils as util
from app.core.config import settings
from app.core.errors import ErrorCode, biz
from app.core.exceptions import BizError
from app.core.security import CurrentUser
from app.db.session import ER_LOCK_NOWAIT, db, error_no, is_deadlock, is_duplicate, marks, retry_deadlock
from app.services import audit, bill_stats, changes, factories, line_void, locks, orders, rules, seg

log = structlog.get_logger()

CHUNK = 2000
RUNNING = "RUNNING"
SUCCESS = "SUCCESS"
FAILED = "FAILED"
#: 早先按「从流水末尾撤销」撤完的任务（只出现在历史任务上）
REVOKED = "REVOKED"
CANCELED = "CANCELED"
SCOPE_BILLS = "BILLS"
SCOPE_LINES = "LINES"
#: 流水来源：MAX 从最大号 + 1 起 / RANGE 手动起止号（空号段或未使用的号，不够时从最大号之后接着编）
SEQ_MAX = "MAX"
SEQ_RANGE = "RANGE"
#: 生成详情、预演里最多列出的空号段数（合计枚数照常给全）
GAP_LIST_MAX = 500

JOB_FIELDS = (
    "id",
    "gen_batch",
    "pi_no",
    "scope",
    "qty",
    "start_seq_dec",
    "end_seq_dec",
    "start_sn",
    "end_sn",
    "status",
    "done_qty",
    "current_sn",
    "error_code",
    "error_msg",
    "created_by",
    "cancel_by",
    "cancel_at",
    "revoke_reason",
    "created_at",
    "started_at",
    "finished_at",
)

_INSERT_ITEM = (
    "INSERT INTO sn_item(sn, pi_no, customer_code, bill_no, bill_seq, material_code, gen_org_code, gen_bill_no, "
    "gen_bill_seq, seq_pi_no, seq_dec, length_text, attach_no, rule_version_id, seq_prefix, gen_batch, status, "
    "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_ALLOC',%s,%s)"
)

#: 大批量生成走后台线程
executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sn-gen")

#: 当场生成排队等名额的上限（秒）：须明显短于页面请求超时（120 秒）；后台任务一直等
SLOT_WAIT_SECONDS = 60

_slots_lock = threading.Lock()
_slots: tuple[int, threading.BoundedSemaphore] | None = None


def _gen_slots() -> threading.BoundedSemaphore:
    """本实例同时写号池的生成名额（SN_GEN_CONCURRENCY）。每个生成占一条事务连接、一条 PI 锁连接，另有进度、
    取消检查的短连接；不设上限时同步接口线程（40）全在生成，连接数翻倍超过 MySQL max_connections，
    号池上的锁冲突、死锁也随并发数上升。配置改了按新值重建（只影响之后取名额的）。"""
    global _slots
    n = settings.gen_concurrency
    with _slots_lock:
        if _slots is None or _slots[0] != n:
            _slots = (n, threading.BoundedSemaphore(n))
        return _slots[1]


_LINE_FIELDS = (
    "bill_no",
    "seq",
    "material_code",
    "attach",
    "length",
    "unit",
    "qty",
    "start_sn",
    "end_sn",
    "rule_label",
)


def item_segs(x: dict) -> list[list[int]]:
    """计划中一个物料行的号段（十进制流水起止，升序）；早先的计划只有一段。"""
    return x.get("segs") or [[x["start_seq"], x["end_seq"]]]


def job_json(j: dict | None) -> dict | None:
    out = util.jrow(j, JOB_FIELDS)
    if out is not None and j is not None:
        plan = json.loads(j["plan_json"])
        # 早先的计划里没有附件项次、单位
        out["lines"] = [
            {k: x.get(k) for k in _LINE_FIELDS}
            | {"revoked": bool(x.get("revoked")), "seg_list": x.get("seg_list") or _one_seg(x)}
            for x in plan["items"]
        ]
        out["bills"] = sorted({x["bill_no"] for x in plan["items"]})
        out["revoked_qty"] = sum(x["qty"] for x in plan["items"] if x.get("revoked"))
        # 确认生成时同前缀别的 PI 先生成了：号段自动顺延到新的最大号之后（与预演时不同）
        out["shifted"] = bool(plan.get("shifted"))
    return out


def _one_seg(x: dict) -> list[dict]:
    return [
        {
            "start_seq": x["start_seq"],
            "end_seq": x["end_seq"],
            "qty": x["qty"],
            "start_sn": x["start_sn"],
            "end_sn": x["end_sn"],
        }
    ]


def limits() -> dict:
    return {
        "default": settings.gen_default_limit,
        "max": settings.gen_max_per_run,
        "sync_threshold": settings.gen_sync_threshold,
    }


def last_seq(pi: str) -> int:
    """本 PI 自己生成的号中最大的流水（统计用；排号按前缀，见 prefix_of / rules.prefix_last）。"""
    return int(db.scalar("SELECT COALESCE(MAX(last_seq_dec), 0) FROM sn_pi_counter WHERE pi_no = %s", pi) or 0)


def prefix_of(pi: str) -> str | None:
    """本 PI 登记的规则前缀（一个 PI 一个前缀，见 rules.pi_rule）；没选规则为 None。"""
    return db.scalar("SELECT prefix FROM sn_pi_rule WHERE pi_no = %s", pi)


# ================================================================== 空号段（按前缀）


def gap_segments(prefix: str | None, last: int | None = None) -> list[tuple[int, int]]:
    """前缀流水 1..最大号之间没有号占用的连续段（升序）：删号腾出、起始号跳过的流水，同前缀各 PI 都能用。"""
    if not prefix:
        return []
    top = rules.prefix_last(prefix) if last is None else last
    if top <= 0:
        return []
    out = [
        (int(r["s"]), int(r["e"]))
        for r in db.all(
            "SELECT prev + 1 s, seq_dec - 1 e FROM (SELECT seq_dec, LAG(seq_dec, 1, 0) OVER (ORDER BY seq_dec) prev "
            "FROM sn_item WHERE seq_prefix = %s AND seq_dec <= %s) t WHERE seq_dec - prev > 1 ORDER BY seq_dec",
            prefix,
            top,
        )
    ]
    used_top = int(
        db.scalar("SELECT COALESCE(MAX(seq_dec), 0) FROM sn_item WHERE seq_prefix = %s AND seq_dec <= %s", prefix, top)
    )
    if used_top < top:
        out.append((used_top + 1, top))
    return out


def gaps(pi_in: str | None, cu: CurrentUser | None = None) -> dict:
    """本 PI 前缀的空号段：起止流水号与枚数（最多列 GAP_LIST_MAX 段，合计照常给全）、前缀最大号与下一个号。"""
    pi = util.trim(pi_in)
    if not pi:
        raise biz(ErrorCode.PI_REQUIRED)
    if cu is not None:
        cu.check_read(
            *(orders.bill_orgs(db.column("SELECT DISTINCT bill_no FROM sn_prd_mo WHERE pi = %s", pi)) or {"-"})
        )
    return gaps_of(pi)


def free_segments(prefix: str | None, last: int | None = None) -> list[dict]:
    """空号段按来源单据拆段（升序）：来源单据 = 流水上最后一次删掉的号的生成单据（gen_bill_no，
    可能是同前缀别的 PI 的）；
    起始号跳过的流水来源单据为空串。"""
    if not prefix:
        return []
    top = rules.prefix_last(prefix) if last is None else last
    raw = gap_segments(prefix, top)
    if not raw:
        return []
    # 只取现在没有号占用的流水；同一流水删过多次取最后一次（号池 id 自增不复用，大的在后）
    marked = [
        (int(r["s"]), int(r["e"]), r["bill_no"])
        for r in db.all(
            "SELECT bill_no, MIN(seq_dec) s, MAX(seq_dec) e FROM ("
            "SELECT d.gen_bill_no bill_no, d.seq_dec, "
            "d.seq_dec - ROW_NUMBER() OVER (PARTITION BY d.gen_bill_no ORDER BY d.seq_dec) g "
            "FROM sn_item_deleted d WHERE d.seq_prefix = %s AND d.seq_dec <= %s "
            "AND d.id = (SELECT MAX(x.id) FROM sn_item_deleted x "
            "WHERE x.seq_prefix = d.seq_prefix AND x.seq_dec = d.seq_dec) "
            "AND NOT EXISTS (SELECT 1 FROM sn_item i WHERE i.seq_prefix = d.seq_prefix AND i.seq_dec = d.seq_dec)"
            ") t GROUP BY bill_no, g ORDER BY s",
            prefix,
            top,
        )
    ]
    marked.sort()
    out: list[tuple[int, int, str]] = []
    i = 0
    for s, e in raw:
        pos = s
        # 删掉的号都没有号占用，连续的一段一定落在同一个空号段里
        while i < len(marked) and marked[i][0] <= e:
            a, b, bill = marked[i]
            if a > pos:
                out.append((pos, a - 1, ""))
            out.append((a, b, bill))
            pos = b + 1
            i += 1
        if pos <= e:
            out.append((pos, e, ""))
    return [{"start_seq": a, "end_seq": b, "qty": b - a + 1, "bill_no": bill} for a, b, bill in out]


def gaps_of(pi: str) -> dict:
    """本 PI 前缀的空号段与前缀最大号（同前缀各 PI 看到的相同）；没选规则时为空。
    reserve_start / reserved*：早先的号段预留已取消，保留字段（空），页面接口不变。"""
    prefix = prefix_of(pi)
    last = rules.prefix_last(prefix)
    segs = free_segments(prefix, last)
    return {
        "pi_no": pi,
        "prefix": prefix,
        "last_seq": last,
        "next_seq": last + 1,
        "count": len(segs),
        "total": sum(x["qty"] for x in segs),
        "segments": segs[:GAP_LIST_MAX],
        "truncated": len(segs) > GAP_LIST_MAX,
        "reserve_start": None,
        "reserved_count": 0,
        "reserved_total": 0,
        "reserved": [],
    }


# ================================================================== 单据号段（生成页单据维度）

#: 单据号段弹窗最多列出的段数（合计照常给全）
SEG_LIST_MAX = 500
#: SN 明细每页最多条数
SN_PAGE_MAX = 200

#: 号段：单据挂在本 PI 下的号按（流水归属 PI，十进制流水）连续合并；g 相同即连续。
#: 只含本系统生成的号：导入的号没有流水，不在生成页（只能在分配页分配）
_ISLAND = (
    "SELECT {cols}, seq_dec - ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY seq_dec) g "
    "FROM sn_item WHERE pi_no = %s AND bill_no IN ({bills}) AND origin = 'GEN'{scope}"
)


def bill_segment_counts(pi: str, bill_nos: list[str], facs: tuple[str, ...] | None) -> dict[str, tuple[int, int]]:
    """各单据现有号的号段数与枚数（生成详情的单据头用）：号段按物料行分开算（与单据号段弹窗同口径）。"""
    if not bill_nos:
        return {}
    isql, iargs = factories.item_scope_sql(facs)
    inner = _ISLAND.format(
        cols="bill_no, bill_seq, seq_pi_no", part="bill_no, bill_seq, seq_pi_no", bills=marks(len(bill_nos)), scope=isql
    )
    return {
        r["bill_no"]: (int(r["segs"]), int(r["qty"]))
        for r in db.all(
            "SELECT bill_no, COUNT(*) segs, SUM(n) qty FROM ("
            f"SELECT bill_no, bill_seq, seq_pi_no, g, COUNT(*) n FROM ({inner}) t "
            "GROUP BY bill_no, bill_seq, seq_pi_no, g"
            ") u GROUP BY bill_no",
            pi,
            *bill_nos,
            *iargs,
        )
    }


def _bill_key(cu: CurrentUser, pi_in: str | None, bill_in: str | None) -> tuple[str, str, tuple[str, ...] | None]:
    pi, bill = util.trim(pi_in), util.trim(bill_in)
    if not pi:
        raise biz(ErrorCode.PI_REQUIRED)
    if not bill:
        raise biz(ErrorCode.ORDER_BILL_REQUIRED)
    cu.check_read(*orders.bill_orgs([bill]))
    return pi, bill, cu.read_factories(None)


def bill_segments(cu: CurrentUser, pi_in: str | None, bill_in: str | None) -> dict:
    """单据号段：已用号段 = 单据挂在本 PI 下现有的号按物料行（单据序号）分开、行内按流水连续合并成段（转入的号
    按流水归属 PI 单独成段），按附件项次（号上的值）从小到大排（与排号同一比较，空的排最后），项次相同按序号（行号），
    同一物料行内本 PI 的在前、再按流水；每段带序号、物料、附件项次；
    空闲号段 = 本单据生成后删掉、现在没有号占用的流水。另给前缀最大号与下一个号（同一前缀各 PI 共用流水，
    页面在底部「流水」显示，不列在单据号段里）。"""
    pi, bill, facs = _bill_key(cu, pi_in, bill_in)
    isql, iargs = factories.item_scope_sql(facs)
    inner = _ISLAND.format(
        cols="sn, bill_seq, material_code, attach_no, seq_pi_no, seq_dec, status",
        part="bill_seq, seq_pi_no",
        bills="%s",
        scope=isql,
    )
    rows = db.all(
        "SELECT bill_seq, MIN(material_code) material_code, MIN(attach_no) attach_no, seq_pi_no, "
        "MIN(seq_dec) s, MAX(seq_dec) e, COUNT(*) n, SUM(status = 'PENDING_ALLOC') pending, "
        "MIN(fs) start_sn, MIN(ls) end_sn FROM (SELECT bill_seq, material_code, attach_no, seq_pi_no, seq_dec, "
        "status, g, FIRST_VALUE(sn) OVER (PARTITION BY bill_seq, seq_pi_no, g ORDER BY seq_dec) fs, "
        "FIRST_VALUE(sn) OVER (PARTITION BY bill_seq, seq_pi_no, g ORDER BY seq_dec DESC) ls "
        f"FROM ({inner}) a) b GROUP BY bill_seq, seq_pi_no, g "
        "ORDER BY bill_seq, seq_pi_no = %s DESC, seq_pi_no, s",
        pi,
        bill,
        *iargs,
        pi,
    )
    use = [
        {
            "bill_seq": int(r["bill_seq"]),
            "material_code": r["material_code"] or "",
            "attach_no": r["attach_no"] or "",
            "seq_pi_no": r["seq_pi_no"],
            "start_seq": int(r["s"]),
            "end_seq": int(r["e"]),
            "start_sn": r["start_sn"],
            "end_sn": r["end_sn"],
            "qty": int(r["n"]),
            "pending": int(r["pending"] or 0),
            "allocated": int(r["n"]) - int(r["pending"] or 0),
            "transferred_in": r["seq_pi_no"] != pi,
        }
        for r in rows
    ]
    # 稳定排序：同一物料行的几段保持 SQL 里的先后（本 PI 的在前、按流水），截断前排好，列出的是排在最前的号段
    use.sort(key=lambda x: (not x["attach_no"].strip(), attach_order(x["attach_no"]), x["bill_seq"]))
    prefix = prefix_of(pi)
    last = rules.prefix_last(prefix)
    free = [x for x in free_segments(prefix, last) if x["bill_no"] == bill]
    return {
        "pi_no": pi,
        "bill_no": bill,
        "last_seq": last,
        "next_seq": last + 1,
        "use": use[:SEG_LIST_MAX],
        "use_count": len(use),
        "use_qty": sum(x["qty"] for x in use),
        "use_truncated": len(use) > SEG_LIST_MAX,
        "free": free[:SEG_LIST_MAX],
        "free_count": len(free),
        "free_qty": sum(x["qty"] for x in free),
        "free_truncated": len(free) > SEG_LIST_MAX,
        # 早先的号段预留已取消：保留字段（空），页面接口不变
        "reserved": [],
        "reserved_qty": 0,
    }


def segment_sns(
    cu: CurrentUser,
    pi_in: str | None,
    bill_in: str | None,
    seq_pi_in: str | None,
    start: int | None,
    end: int | None,
    page: int | None,
    size: int | None,
    bill_seq: int | None = None,
) -> dict:
    """号段 SN 明细：单据挂在本 PI 下、流水归属 seq_pi_no、流水在 start～end 的号，按流水排，分页。
    bill_seq：只看该物料行（单据序号）的号（已用号段按物料行分开列出，点明细时带上）；不给为整张单据。"""
    pi, bill, facs = _bill_key(cu, pi_in, bill_in)
    seq_pi = util.trim(seq_pi_in) or pi
    if start is None or end is None or start < 1 or end < start:
        raise biz(ErrorCode.GEN_SEQ_RANGE_INVALID)
    pg = max(1, page or 1)
    sz = min(max(1, size or 20), SN_PAGE_MAX)
    isql, iargs = factories.item_scope_sql(facs)
    where = (
        "FROM sn_item WHERE pi_no = %s AND bill_no = %s AND seq_pi_no = %s AND seq_dec BETWEEN %s AND %s "
        "AND origin = 'GEN'" + ("" if bill_seq is None else " AND bill_seq = %s") + isql
    )
    args = [pi, bill, seq_pi, start, end, *([] if bill_seq is None else [bill_seq]), *iargs]
    total = db.count("SELECT COUNT(*) " + where, *args)
    items = db.column("SELECT sn " + where + " ORDER BY seq_dec LIMIT %s OFFSET %s", *args, sz, (pg - 1) * sz)
    return {"total": total, "page": pg, "size": sz, "items": items}


def seq_choice(
    mode: str | None, start: int | None = None, end: int | None = None, gaps: list[list[int]] | None = None
) -> dict:
    """规范化流水来源：MAX（默认）/ RANGE（起止流水号为正整数，止号不小于起号；只填起号按单个号）。
    gaps：RANGE 时范围不够、剩下的号要用的空号段（[[起, 止], …]，按从小到大使用，每段从最小号用起）；
    不给为剩下的接在最大号之后。"""
    m = util.trim(mode) or SEQ_MAX
    if m == SEQ_MAX:
        return {"mode": SEQ_MAX}
    if m != SEQ_RANGE:
        raise biz(ErrorCode.GEN_SEQ_RANGE_INVALID)
    if end is None:
        end = start
    if start is None or start < 1 or end < start:
        raise biz(ErrorCode.GEN_SEQ_RANGE_INVALID)
    out = {"mode": SEQ_RANGE, "start": int(start), "end": int(end)}
    picked = []
    for g in gaps or []:
        if not isinstance(g, (list, tuple)) or len(g) != 2:
            raise biz(ErrorCode.GEN_SEQ_RANGE_INVALID)
        a, b = g
        if not isinstance(a, int) or not isinstance(b, int) or a < 1 or b < a:
            raise biz(ErrorCode.GEN_SEQ_RANGE_INVALID)
        picked.append([a, b])
    if picked:
        out["gaps"] = [list(g) for g in seg.merge(picked)]
    return out


class _Taker:
    """按流水来源依次切号：先用 free（升序的空闲段），用完从 overflow 起接着编。"""

    def __init__(self, free: list[tuple[int, int]], overflow: int) -> None:
        self.free = free
        self.i = 0
        self.pos = free[0][0] if free else 0
        self.overflow = overflow
        self.in_range = 0
        self.over = 0

    def take(self, n: int, floor: int | None = None) -> list[list[int]]:
        """切 n 个号。floor：从 overflow 接着编时第一个号不小于它（自定义规则的起始号），跳过的流水成为空号段。"""
        out: list[list[int]] = []
        while n > 0:
            if self.i < len(self.free):
                s, e = self.free[self.i]
                a = max(s, self.pos)
                b = min(e, a + n - 1)
                self.in_range += b - a + 1
                if b == e:
                    self.i += 1
                    self.pos = self.free[self.i][0] if self.i < len(self.free) else 0
                else:
                    self.pos = b + 1
            else:
                a = max(self.overflow, floor or 0)
                b = a + n - 1
                self.over += n
                self.overflow = b + 1
            n -= b - a + 1
            if out and out[-1][1] + 1 == a:
                out[-1][1] = b
            else:
                out.append([a, b])
        return out


def _assign(prefix: str, todo: list[Line], last: int, seq: dict) -> dict:
    """按排号顺序给每个物料行切号（同一前缀下各 PI 共用一套流水，last 为前缀最大号）：
    - MAX：依次接在前缀最大号之后；
    - RANGE：先用起止号内空着的流水，再用所选空号段，仍不够的接在 max(最大号, 止号) 之后。
    起始号（PI 登记的）：接在最大号之后时第一个号不小于它，跳过的流水成为空号段。
    返回各行号段与来源、生成后的最大号与各来源枚数。"""
    unused = seg.merge(gap_segments(prefix, last) + [(last + 1, seg.INF)])
    floor = next((x.res.seq_start for x in todo if x.res.seq_start), None)
    lines: dict[tuple[str, int], dict] = {}
    range_qty = gap_qty = over = 0
    if seq["mode"] == SEQ_RANGE:
        lo, hi = seq["start"], seq["end"]
        in_range = seg.intersect(unused, [(lo, hi)])
        picked = seg.merge(seq.get("gaps") or [])
        for a, b in picked:
            if seg.subtract([(a, b)], seg.intersect(unused, [(1, last)])):
                raise biz(ErrorCode.GEN_GAP_INVALID, start=a, end=b)
        extra = seg.subtract(picked, in_range)
        tail_from = max(last, hi) + 1
        taker = _Taker(in_range + extra, tail_from)
        for x in todo:
            segs = taker.take(x.quota, floor)
            tail = seg.total(seg.intersect([tuple(g) for g in segs], [(tail_from, seg.INF)]))
            gap = seg.total(seg.intersect([tuple(g) for g in segs], extra))
            range_qty += seg.total(seg.intersect([tuple(g) for g in segs], in_range))
            gap_qty += gap
            over += tail
            lines[(x.bill_no, x.seq)] = {"segs": segs, "src": SEQ_RANGE, "tail": tail, "gap": gap}
    else:
        tail_from = last + 1
        taker = _Taker([], tail_from)
        for x in todo:
            segs = taker.take(x.quota, floor)
            over += x.quota
            lines[(x.bill_no, x.seq)] = {"segs": segs, "src": "TAIL", "tail": x.quota, "gap": 0}
    top = max([last] + [g[1] for v in lines.values() for g in v["segs"]])
    return {
        "lines": lines,
        "top": top,
        "range_qty": range_qty,
        "gap_qty": gap_qty,
        "overflow_qty": over,
        "tail_from": max(tail_from, floor or 0),
    }


# ================================================================== 物料行与生成条件


@dataclass
class Line:
    """一条参与生成的物料行。"""

    bill_no: str
    seq: int
    material_code: str
    customer_code: str
    org_code: str
    pi: str
    qty: int
    own: int
    length: str
    length_source: str
    unit: str
    unit_source: str
    attach: str
    res: rules.Resolved | None
    block: str | None = None
    issues: list[str] = field(default_factory=list)
    #: 物料行失效（line_void）：不参与排号，额度按 0 算
    void: bool = False
    #: 项次拦截（ATTACH_ORDER_BLOCKED）：挡住本行的物料行（同前缀别的 PI 或本 PI 这次不生成的行）
    blockers: list[dict] = field(default_factory=list)

    @property
    def quota(self) -> int:
        return 0 if self.void else max(0, self.qty - self.own)


def _load_lines(pi: str, keys: list[tuple[str, int]]) -> list[Line]:
    """按给定顺序取物料行（快照 + 计数 + 附件项次 + 规则 + 单据冻结），并判断生成条件。"""
    return load_lines_of({pi: keys})[pi]


def load_lines_of(groups: dict[str, list[tuple[str, int]]]) -> dict[str, list[Line]]:
    """多张 PI 的 _load_lines 一次取（生成页列表逐 PI 判断「需先选规则 / 不能生成」用）：快照、计数、附件项次、
    米数、产品单位、单据冻结、失效条件各一条 SQL，PI 的规则登记按 PI 查，项次拦截按前缀查一次；
    每张 PI 的结果与单独调用 _load_lines 相同。每组的物料行视为一次生成（彼此不挡）。"""
    out: dict[str, list[Line]] = {pi: [] for pi in groups}
    all_keys = [k for keys in groups.values() for k in keys]
    if not all_keys:
        return out
    bills = sorted({k[0] for k in all_keys})
    snap = {
        (r["bill_no"], r["seq"]): r
        for r in db.all(f"SELECT * FROM sn_prd_mo WHERE bill_no IN ({marks(len(bills))})", *bills)
    }
    counts, fills, lens, units = orders.line_extras(all_keys)
    blocks = changes.generation_blocks(bills)
    vrule = line_void.current()
    regs = rules.pi_rules([pi for pi, keys in groups.items() if keys])
    # 前缀能否用的判断结果、各前缀的项次拦截状态（同一次判断里共用）
    taken: dict = {}
    for pi, keys in groups.items():
        if not keys:
            continue
        reg = regs.get(pi)
        res = rules._registered(pi, reg)
        for k in keys:
            r = snap.get(k)
            if r is None:
                raise biz(ErrorCode.ORDER_LINE_NOT_FOUND, bill_no=k[0], seq=k[1])
            if r["pi"] != pi:
                raise biz(ErrorCode.GEN_SCOPE_INVALID)
            c = counts.get(k)
            attach, _src = orders.effective_attach(r, fills.get(k))
            length, length_src = orders.effective_length(r, lens.get(k))
            unit, unit_src = orders.effective_unit(r, units.get(k))
            line = Line(
                r["bill_no"],
                r["seq"],
                r["material_code"],
                r["customer_code"],
                r["org_code"],
                r["pi"],
                orders.sn_qty(r),
                0 if c is None else int(c["own_generated"]),
                length,
                length_src,
                unit,
                unit_src,
                attach,
                res,
                blocks[r["bill_no"]],
                void=vrule.is_void(r),
            )
            _check(line, reg, taken)
            out[pi].append(line)
        if reg is not None:
            _check_order(reg["prefix"], out[pi], set(keys), taken)
    return out


def _check(line: Line, reg: dict | None, taken: dict) -> None:
    """生成条件（只对还有额度的物料行）。reg：本 PI 的规则登记；taken：前缀能否用的判断结果（同一次判断里共用）。"""
    if line.quota <= 0:
        return
    if line.block:
        line.issues.append(line.block)
    if line.res is None:
        line.issues.append("NO_RULE")
    elif not line.res.ready:
        line.issues.append("RULE_PREFIX")
    elif line.res.plan in (rules.PLAN_NEW, rules.PLAN_CLAIM, rules.PLAN_SHARE) and not _prefix_ok(line, taken):
        # 自定义规则选定后（或规则库版本首次归属本 PI、共用别的 PI 的前缀前）前缀被尚未归属的版本占用、
        # 与别的前缀一个是另一个的开头，或已按别的进制 / 位数用了这个前缀：须改前缀，免得生成时才失败
        line.issues.append("RULE_PREFIX")
    elif reg is not None and line.res.spec().prefix != reg["prefix"]:
        # 一个 PI 一个规则前缀：规则在规则库换了前缀（另出版本）后与本 PI 登记的前缀不同，须重新选规则
        line.issues.append("RULE_PI_PREFIX")
    spec = line.res.spec() if line.res is not None and line.res.ready else None
    # 米数、产品单位用金蝶原值（含中文也照用）；金蝶为空且页面未填写时须先填写（规则不带米数 / 单位时不影响）
    if line.length_source == orders.LENGTH_EMPTY and (spec is None or spec.with_length):
        line.issues.append("LENGTH_EMPTY")
    if spec is not None and spec.with_unit and line.unit_source == orders.UNIT_EMPTY:
        line.issues.append("UNIT_EMPTY")
    # 附件项次必填，且须为数字（按数字排先后）
    if not line.attach:
        line.issues.append("ATTACH_EMPTY")
    elif attach_key(line.attach) is None:
        line.issues.append("ATTACH_NOT_NUMERIC")


def _prefix_ok(line: Line, memo: dict | None) -> bool:
    """前缀能否给本 PI 用（同一次判断里同一前缀、格式只查一次）；规则库版本首次归属时不算它自己。"""
    spec = line.res.spec()
    own = line.res.version["id"] if line.res.plan == rules.PLAN_CLAIM else None
    k = (spec.prefix, spec.base, spec.seq_len, spec.charset, line.pi, own)
    if memo is None or k not in memo:
        ok = rules.prefix_usable(spec.prefix, spec, line.pi, own)
        if memo is None:
            return ok
        memo[k] = ok
    return memo[k]


_ATTACH_NUM = re.compile(r"^\d+(\.\d+)?$")


def attach_key(attach: str | None) -> Decimal | None:
    """附件项次的数值（"01" 与 "1" 相同）；为空或不是数字时 None（不能生成，见 _check）。"""
    v = (attach or "").strip()
    return Decimal(v) if _ATTACH_NUM.match(v) else None


def attach_order(attach: str) -> tuple:
    """附件项次的排序次序：按数字比大小（"2" 在 "10" 前，"01" 与 "1" 相同）；不是数字的排在数字之后、按文字比。"""
    k = attach_key(attach)
    if k is not None:
        return (0, k, "")
    return (1, Decimal(0), (attach or "").strip())


# ================================================================== 项次拦截（按前缀）

#: 被拦时最多列出的挡路物料行
BLOCKER_LIST_MAX = 20


def _prefix_order_state(prefix: str, memo: dict) -> tuple[set[Decimal], list[dict]]:
    """前缀的项次状态：(已开始的项次, 候选挡路行)。
    - 已开始的项次：这个前缀下现存的号（本系统生成的）的附件项次；号全删掉的项次回到没开始。
    - 候选挡路行：登记了这个前缀的全部 PI 的物料行中还有额度的；失效的物料行、冻结或暂停交付单据的行、
      删过号的行（删号后重新生成接在最大号之后）不挡。项次为空或不是数字的 key 为 None（挡全部没开始的项次）。"""
    mk = ("order", prefix)
    if mk in memo:
        return memo[mk]
    started = {
        k
        for k in (
            attach_key(a)
            for a in db.column(
                "SELECT DISTINCT attach_no FROM sn_item WHERE seq_prefix = %s AND origin = 'GEN'", prefix
            )
        )
        if k is not None
    }
    snap = db.all(
        "SELECT m.* FROM sn_prd_mo m JOIN sn_pi_rule r ON r.pi_no = m.pi WHERE r.prefix = %s "
        "ORDER BY m.pi, m.bill_no, m.seq",
        prefix,
    )
    cands: list[dict] = []
    if snap:
        keys = [(r["bill_no"], r["seq"]) for r in snap]
        counts, fills, _lens, _units = orders.line_extras(keys)
        bills = sorted({k[0] for k in keys})
        ph = marks(len(bills))
        held = set(
            db.column(
                f"SELECT bill_no FROM sn_bill_ledger WHERE bill_no IN ({ph}) "
                "AND (pi_frozen <> 0 OR suspend IS NOT NULL)",
                *bills,
            )
        )
        deleted = {
            (r["bill_no"], int(r["bill_seq"]))
            for r in db.all(
                f"SELECT DISTINCT bill_no, bill_seq FROM sn_item_deleted WHERE bill_no IN ({ph})",
                *bills,
            )
        }
        vrule = line_void.current()
        for r in snap:
            k = (r["bill_no"], r["seq"])
            c = counts.get(k)
            quota = orders.sn_qty(r) - (0 if c is None else int(c["own_generated"]))
            if quota <= 0 or vrule.is_void(r) or r["bill_no"] in held or k in deleted:
                continue
            attach, _src = orders.effective_attach(r, fills.get(k))
            ak = attach_key(attach)
            cands.append(
                {
                    "pi_no": r["pi"],
                    "bill_no": r["bill_no"],
                    "seq": r["seq"],
                    "material_code": r["material_code"],
                    "attach": attach,
                    "quota": quota,
                    "key": ak,
                    "reason": "ATTACH_EMPTY"
                    if not attach
                    else ("ATTACH_NOT_NUMERIC" if ak is None else "NOT_GENERATED"),
                }
            )
    memo[mk] = (started, cands)
    return memo[mk]


def _check_order(prefix: str, ls: list[Line], run: set[tuple[str, int]], memo: dict) -> None:
    """项次拦截：同一前缀下，一行的项次已开始（有现存的号）时不拦，接在最大号之后生成；还没开始时，
    只要登记了这个前缀的 PI 里还有更小项次（或项次为空、不是数字）的行有额度，就拦住（ATTACH_ORDER_BLOCKED）。
    run 为这次一起生成的行：彼此不挡（同一次生成里小项次排在前）。"""
    todo = [x for x in ls if x.quota > 0 and attach_key(x.attach) is not None]
    if not todo:
        return
    started, cands = _prefix_order_state(prefix, memo)
    others = [b for b in cands if (b["bill_no"], b["seq"]) not in run]
    if not others:
        return
    for x in todo:
        k = attach_key(x.attach)
        if k in started:
            continue
        bl = [b for b in others if b["key"] is None or b["key"] < k]
        if bl:
            x.issues.append("ATTACH_ORDER_BLOCKED")
            x.blockers = [
                {n: b[n] for n in ("pi_no", "bill_no", "seq", "material_code", "attach", "quota", "reason")}
                for b in bl[:BLOCKER_LIST_MAX]
            ]


def _gen_order(ls: list[Line], scope: str) -> list[Line]:
    """排号顺序：先按附件项次从小到大（attach_order）。
    - 点选物料行（LINES）：同项次严格按点击先后（ls 即点击顺序），不按单据分组；
    - 勾整张单据（BILLS）：同项次按单据勾选先后（第一次出现的先后），同一单据内按行号。"""
    if scope == SCOPE_LINES:
        idx = {id(x): i for i, x in enumerate(ls)}
        return sorted(ls, key=lambda x: (attach_order(x.attach), idx[id(x)]))
    rank: dict[str, int] = {}
    for x in ls:
        rank.setdefault(x.bill_no, len(rank))
    return sorted(ls, key=lambda x: (attach_order(x.attach), rank[x.bill_no], x.seq))


def _scope_keys(pi: str, scope: str, bills_in: list[str] | None, lines_in: list[dict] | None) -> list[tuple[str, int]]:
    if scope == SCOPE_BILLS:
        nos = list(dict.fromkeys(util.trim(b) for b in bills_in or [] if util.trim(b)))
        if not nos:
            raise biz(ErrorCode.GEN_SCOPE_INVALID)
        keys = []
        for no in nos:
            b = orders.must_bill(no)
            if b.pi != pi:
                raise biz(ErrorCode.GEN_SCOPE_INVALID)
            keys += [(no, x["seq"]) for x in b.lines]
        return keys
    if scope == SCOPE_LINES:
        keys = list(dict.fromkeys((util.trim(x.get("bill_no")), int(x.get("seq") or 0)) for x in lines_in or []))
        if not keys:
            raise biz(ErrorCode.GEN_SCOPE_INVALID)
        return keys
    raise biz(ErrorCode.GEN_SCOPE_INVALID)


# ================================================================== 预演


def _plan(pi: str, scope: str, keys: list[tuple[str, int]], seq: dict | None = None) -> dict:
    """按排号顺序（见 _gen_order）给每个还有额度的物料行切号（整行额度，不拆行），接在本 PI 规则前缀的最大号之后；
    流水来源见 seq（默认 MAX）。返回计划（不落库）；不能生成时 issues 非空（项次拦截另带挡路的物料行）。"""
    seq = seq or {"mode": SEQ_MAX}
    prefix = prefix_of(pi)
    base = rules.prefix_last(prefix)
    ls = _gen_order(_load_lines(pi, keys), scope)
    todo = [x for x in ls if x.quota > 0]
    quota = sum(x.quota for x in todo)
    issues = [
        {
            "bill_no": x.bill_no,
            "seq": x.seq,
            "material_code": x.material_code,
            "issues": x.issues,
            # 自定义前缀被占用时给出前缀，确认生成时按前缀冲突提示
            "prefix": x.res.version["prefix"] if "RULE_PREFIX" in x.issues and x.res.ready else None,
            # 项次拦截：挡住本行的物料行（最多 BLOCKER_LIST_MAX 行）
            "blockers": x.blockers,
        }
        for x in todo
        if x.issues
    ]
    out = {
        "pi_no": pi,
        "scope": scope,
        "quota": quota,
        "issues": issues,
        "items": [],
        # 前缀最大号（生成时按最新的最大号接号，只是起号变了时自动顺延，见 _generate_in_tx）
        "base_last_seq": base,
        "prefix": prefix,
        "seq": seq,
    }
    if not todo:
        out["issues"] = [{"bill_no": None, "seq": None, "material_code": None, "issues": ["QUOTA_EMPTY"]}]
        return out
    if issues:
        return out
    qty = quota
    if qty > settings.gen_max_per_run:
        raise biz(ErrorCode.GEN_QTY_OVER_MAX, qty=qty, max=settings.gen_max_per_run)
    got = _assign(prefix, todo, base, seq)
    for x in todo:
        n = x.quota
        spec = x.res.spec()
        a_ = got["lines"][(x.bill_no, x.seq)]
        segs = [list(g) for g in a_["segs"]]
        end = segs[-1][1]
        grow = x.res.seq_grow
        # 上限自动突破：用完规则位数后接着加位（最多 12 位），号段按位数切开，每段一个位数（生成时挂对应位数的版本）
        mx = codec.max_seq_grow(spec) if grow else codec.max_seq(spec)
        if end > mx:
            raise biz(ErrorCode.GEN_RULE_EXHAUSTED, max=mx, end=end)
        if grow:
            parts = [(a, b, w) for s0, e0 in segs for a, b, w in codec.split_by_width(s0, e0, spec)]
            segs = [[a, b] for a, b, _ in parts]
        else:
            parts = [(a, b, spec.seq_len) for a, b in segs]
        length = x.length if spec.with_length else None
        unit = x.unit if spec.with_unit else None
        res = x.res
        seg_list = []
        for a, b, w in parts:
            ws = codec.widen(spec, w)
            g = {
                "start_seq": a,
                "end_seq": b,
                "qty": b - a + 1,
                "start_sn": codec.format_sn(a, ws, length, unit),
                "end_sn": codec.format_sn(b, ws, length, unit),
                "start_seq_text": codec.encode_seq(a, ws),
                "end_seq_text": codec.encode_seq(b, ws),
            }
            if grow:
                # 这一段的流水位数（确认生成弹窗按段显示）
                g["seq_len"] = w
            # 突破后位数变多，SN 也变长：每段的止号都不能超过 SN 长度上限
            if len(g["end_sn"]) > codec.SN_MAX:
                raise biz(ErrorCode.RULE_SN_TOO_LONG)
            seg_list.append(g)
        start_sn = seg_list[0]["start_sn"]
        out["items"].append(
            {
                "bill_no": x.bill_no,
                "seq": x.seq,
                "material_code": x.material_code,
                "customer_code": x.customer_code,
                "org_code": x.org_code,
                "length": length,
                "unit": unit,
                "attach": x.attach,
                # 确认生成弹窗展示用（不参与预演 / 生成比对）：前缀、进制、流水位数、起止流水号
                "prefix": spec.prefix,
                "base": spec.base,
                "seq_len": spec.seq_len,
                # 流水上限自动突破（开启时号段按位数切开，seg_list 各段另带 seq_len）
                "seq_grow": grow,
                "start_seq_text": seg_list[0]["start_seq_text"],
                "end_seq_text": seg_list[-1]["end_seq_text"],
                "line_qty": x.qty,
                "own_before": x.own,
                "qty": n,
                # 号的来源：RANGE 手动起止号 / TAIL 接在前缀最大号之后；
                # tail_qty 接在最大号之后的枚数，gap_qty 用所选空号段的枚数（手动起止号）
                "seq_src": a_["src"],
                "tail_qty": a_["tail"],
                "gap_qty": a_.get("gap", 0),
                # 号段：一段或几段不连续的号（用空号段时）；start_seq / end_seq 为第一段起号、最后一段止号
                "segs": segs,
                "seg_list": seg_list,
                "start_seq": segs[0][0],
                "end_seq": end,
                "start_sn": start_sn,
                "end_sn": seg_list[-1]["end_sn"],
                "rule_key": res.key(),
                "rule_plan": res.plan,
                "rule_id": None if res.rule is None else res.rule["id"],
                "version_id": res.version["id"],
                "custom": res.custom,
                "rule_label": (
                    f"{res.rule['rule_code']} v{res.version['version']}" if res.rule is not None else "自定义"
                )
                + f"（{rules.format_text(spec)}）",
            }
        )
    out["qty"] = qty
    out["start_seq"] = min(x["start_seq"] for x in out["items"])
    out["end_seq"] = max(x["end_seq"] for x in out["items"])
    # 生成后的前缀最大号（只增不减）
    out["new_last"] = max(got["top"], out["end_seq"])
    # 用到的空闲流水（手动起止号内）、所选空号段与溢出（从最大号之后接着编）的枚数
    out["range_qty"] = got["range_qty"]
    out["gap_qty"] = got["gap_qty"]
    out["overflow_qty"] = got["overflow_qty"]
    # 接在最大号之后的号从哪个流水起
    out["tail_from"] = got["tail_from"]
    # 早先的共用前缀、号段预留字段已取消（同一前缀各 PI 共用一套流水）：保留字段，页面接口不变
    out["shared_pis"] = []
    out["reserve"] = None
    out["reserve_start"] = None
    out["start_sn"] = out["items"][0]["start_sn"]
    out["end_sn"] = out["items"][-1]["end_sn"]
    return out


def preview(
    cu: CurrentUser,
    pi_in: str | None,
    scope: str | None,
    bills: list[str] | None,
    lines: list[dict] | None,
    seq_mode: str | None = None,
    seq_start: int | None = None,
    seq_end: int | None = None,
    seq_gaps: list[list[int]] | None = None,
) -> dict:
    """预演：范围内每个还有额度的物料行整行生成。可生成时返回令牌与号段；不能生成时 generatable = false 并逐行列出原因
    （可缩小范围后再预演）。流水来源 seq_mode：MAX 从最大号 + 1 起（默认）/ RANGE 用起止流水号内的空闲流水，
    不够时从最大号之后接着编。返回里另带本 PI 的空号段（gaps）。"""
    pi = util.trim(pi_in)
    if not pi:
        raise biz(ErrorCode.PI_REQUIRED)
    sc = util.trim(scope) or SCOPE_BILLS
    seq = seq_choice(seq_mode, seq_start, seq_end, seq_gaps)
    keys = _scope_keys(pi, sc, bills, lines)
    cu.check_write(*orders.bill_orgs(k[0] for k in keys))
    plan = _plan(pi, sc, keys, seq)
    gap = gaps_of(pi)
    if plan["issues"]:
        return {**plan, "generatable": False, "limits": limits(), "gaps": gap}
    now = util.now()
    token = util.token_hex()
    expires = now + timedelta(minutes=settings.preview_ttl_minutes)
    stored = {**plan, "keys": [list(k) for k in keys]}
    db.exec(
        "INSERT INTO sn_gen_preview(token, pi_no, qty, base_last_seq, plan_json, created_by, created_at, expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        token,
        pi,
        plan["qty"],
        plan["base_last_seq"],
        json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
        cu.emp_no,
        now,
        expires,
    )
    return {
        **plan,
        "generatable": True,
        "token": token,
        "expires_at": util.fmt(expires),
        "limits": limits(),
        "gaps": gap,
    }


#: 预演与生成时须相同的字段（号段可以顺延，见 _same_plan）
_KEY_FIELDS = (
    "bill_no",
    "seq",
    "qty",
    "rule_key",
    "rule_plan",
    "line_qty",
    "own_before",
)


def _fixed_segs(plan: dict, x: dict) -> list[tuple[int, int]]:
    """手动起止号（RANGE）时该行落在起止号内、所选空号段里的号（不能顺延）；MAX 时为空。"""
    sq = plan.get("seq") or {}
    if sq.get("mode") != SEQ_RANGE:
        return []
    zone = [(sq["start"], sq["end"])] + [tuple(g) for g in sq.get("gaps") or []]
    return seg.intersect([tuple(g) for g in x["segs"]], zone)


def _same_plan(a: dict, b: dict) -> bool:
    """预演 a 与按最新数据算的 b 是否同一计划：物料行（顺序）、数量、规则相同；手动起止号内、所选空号段里的号相同。
    只是接在最大号之后的号变了（同前缀别的 PI 先生成，最大号变大）不算变：生成时自动顺延（_generate_in_tx）。"""
    if len(a["items"]) != len(b["items"]):
        return False
    for x, y in zip(a["items"], b["items"], strict=True):
        if any(x.get(k) != y.get(k) for k in _KEY_FIELDS) or _fixed_segs(a, x) != _fixed_segs(b, y):
            return False
    return True


def _shifted(a: dict, b: dict) -> bool:
    return any(x["segs"] != y["segs"] for x, y in zip(a["items"], b["items"], strict=True))


# ================================================================== 生成


def generate(cu: CurrentUser, token: str | None) -> dict:
    """凭预演令牌生成。占用 PI 操作锁（后台任务结束时释放）。"""
    p = None if util.blank(token) else db.one("SELECT * FROM sn_gen_preview WHERE token = %s", util.trim(token))
    # 同前缀别的 PI 在预演后先生成（前缀最大号变大）：物料行、数量、规则不变时自动顺延到新的最大号之后，
    # 结果里给出实际号段（shifted）；手动起止号内的号被占用、项次拦截等变了时报预演已失效
    if p is None or p["created_by"] != cu.emp_no:
        raise biz(ErrorCode.GEN_PREVIEW_REQUIRED)
    if db.exists("SELECT EXISTS(SELECT 1 FROM sn_gen_job WHERE preview_token = %s)", p["token"]):
        raise biz(ErrorCode.GEN_PREVIEW_USED)
    if util.now() > p["expires_at"]:
        raise biz(ErrorCode.GEN_PREVIEW_EXPIRED)
    pi = p["pi_no"]
    held = locks.pi_lock(pi)
    try:
        stored = json.loads(p["plan_json"])
        keys = [tuple(k) for k in stored["keys"]]
        cu.check_write(*orders.bill_orgs(k[0] for k in keys))
        fresh = _plan(pi, stored["scope"], keys, stored.get("seq"))
        # 确认生成时再查一次前缀能否用：自定义前缀在预演后被别的规则占用（尚未归属的版本，或别的 PI 按别的进制 /
        # 位数用了）时报前缀冲突 / 格式锁定（页面弹窗提示）
        for i in fresh["issues"]:
            if i.get("prefix"):
                ln = _load_lines(pi, [(i["bill_no"], i["seq"])])
                spec = ln[0].res.spec() if ln and ln[0].res is not None and ln[0].res.ready else None
                rules.check_prefix(i["prefix"], spec=spec, pi=pi)
        if fresh["issues"] or not _same_plan(stored, fresh):
            raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
        batch = util.next_id("G") + util.token_hex()[:4]
        try:
            job_id = db.insert(
                "INSERT INTO sn_gen_job(gen_batch, pi_no, scope, qty, start_seq_dec, end_seq_dec, start_sn, end_sn, "
                "status, done_qty, running_pi, preview_token, plan_json, created_by, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s)",
                batch,
                pi,
                stored["scope"],
                fresh["qty"],
                fresh["start_seq"],
                fresh["end_seq"],
                fresh["start_sn"],
                fresh["end_sn"],
                RUNNING,
                pi,
                p["token"],
                json.dumps(
                    {**fresh, "keys": stored["keys"], "shifted": _shifted(stored, fresh)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                cu.emp_no,
                util.now(),
            )
        except pymysql.err.IntegrityError as e:
            if not is_duplicate(e):
                raise
            if "ux_job_preview" in str(e):
                raise biz(ErrorCode.GEN_PREVIEW_USED) from None
            raise biz(ErrorCode.GEN_BUSY, pi=pi) from None
    except BaseException:
        held.release()
        raise
    if fresh["qty"] <= settings.gen_sync_threshold:
        try:
            _run(cu, job_id, True)
        finally:
            held.release()
    else:

        def background() -> None:
            try:
                _run(cu, job_id, False)
            finally:
                held.release()

        executor.submit(background)
    return job_json(db.one("SELECT * FROM sn_gen_job WHERE id = %s", job_id))


def _run(cu: CurrentUser, job_id: int, rethrow: bool) -> None:
    j = db.one("SELECT * FROM sn_gen_job WHERE id = %s", job_id)
    plan = json.loads(j["plan_json"])
    slots = _gen_slots()
    got = False
    try:
        # 当场生成最多等 SLOT_WAIT_SECONDS；后台任务排队等到名额为止（期间可取消）
        got = slots.acquire(timeout=SLOT_WAIT_SECONDS if rethrow else None)
        if not got:
            raise biz(ErrorCode.CONFLICT_RETRY)
        db.exec("UPDATE sn_gen_job SET started_at = %s WHERE id = %s", util.now(), job_id)
        attempt = 0

        def once() -> None:
            nonlocal attempt
            attempt += 1
            if attempt > 1:
                log.warning("gen_deadlock_retry", job=job_id, pi=j["pi_no"], attempt=attempt)
            with db.tx():
                _generate_in_tx(cu, j, plan)
                # 实际生成的号段（自动顺延时与预演不同）随状态一起写
                n = db.exec(
                    "UPDATE sn_gen_job SET status = %s, done_qty = qty, start_seq_dec = %s, end_seq_dec = %s, "
                    "start_sn = %s, end_sn = %s, current_sn = %s, plan_json = %s, running_pi = NULL, finished_at = %s "
                    "WHERE id = %s AND status = %s AND cancel_at IS NULL",
                    SUCCESS,
                    plan["start_seq"],
                    plan["end_seq"],
                    plan["start_sn"],
                    plan["end_sn"],
                    plan["end_sn"],
                    json.dumps(plan, ensure_ascii=False, separators=(",", ":")),
                    util.now(),
                    job_id,
                    RUNNING,
                )
                if n != 1:
                    if _cancel_requested(job_id):
                        raise biz(ErrorCode.GEN_CANCELED)
                    raise biz(ErrorCode.CONFLICT_RETRY)

        # 死锁时整笔已回滚（号、计数、规则版本、留痕都没写进去），PI 操作锁仍在手里，按原计划整笔重做
        retry_deadlock(once)
        log.info("gen_done", job=job_id, pi=j["pi_no"], qty=j["qty"], attempts=attempt)
    except Exception as e:  # noqa: BLE001
        code = e.code if isinstance(e, BizError) else "INTERNAL"
        canceled = code == ErrorCode.GEN_CANCELED.name
        msg = util.cut(str(e) or type(e).__name__, 1000)
        try:
            db.exec(
                "UPDATE sn_gen_job SET status = %s, done_qty = 0, error_code = %s, error_msg = %s, running_pi = NULL, "
                "finished_at = %s WHERE id = %s AND status = %s",
                CANCELED if canceled else FAILED,
                code,
                msg,
                util.now(),
                job_id,
                RUNNING,
            )
        except Exception:  # noqa: BLE001
            log.exception("gen_job_status_update_failed", job=job_id)
        # 取消、后台失败：涉及的单据各留一条失败痕迹（同步执行的失败由全局异常处理记）
        if canceled or not rethrow:
            for no, items in _by_bill(plan["items"]).items():
                audit.record_isolated(
                    cu,
                    audit.entry(audit.SN_GENERATE)
                    .fail(msg)
                    .pi(j["pi_no"])
                    .bill(no)
                    .factory(items[0]["org_code"])
                    .count(sum(x["qty"] for x in items))
                    .range(items[0]["start_sn"], items[-1]["end_sn"])
                    .batch(j["gen_batch"])
                    .info(f"job={job_id}"),
                )
        if not isinstance(e, BizError):
            log.error("gen_failed", job=job_id, pi=j["pi_no"], deadlock=is_deadlock(e), exc_info=e)
        if rethrow:
            raise
    finally:
        if got:
            slots.release()


def _by_bill(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in items:
        out.setdefault(x["bill_no"], []).append(x)
    return out


def _lock_row(table: str, where: str, args: tuple, insert_sql: str, insert_args: tuple) -> dict:
    """计数行加锁（没有先建）。先不加锁地看行在不在，在就按主键加行锁，不在才插入：
    「INSERT … ON DUPLICATE KEY UPDATE」碰上已有的行、或对不存在的行 FOR UPDATE，都会在主键上加间隙锁，
    几张 PI 同时生成时相邻单据 / PI 的计数行互相卡住，成为死锁。这些行只在 PI 操作锁内变动。"""
    if not db.exists(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {where})", *args):
        db.exec(insert_sql, *insert_args)
    return db.one(f"SELECT * FROM {table} WHERE {where} FOR UPDATE", *args)


def _generate_in_tx(cu: CurrentUser, j: dict, plan: dict) -> None:
    """生成事务。plan 为任务里存的计划，按最新数据重算后就地换成实际生成的计划（自动顺延时号段变了）。"""
    now = util.now()
    pi = j["pi_no"]
    prefix = plan.get("prefix") or plan["items"][0]["prefix"]
    # 先拿前缀锁（锁到事务结束）：同一前缀各 PI 的生成串行，后到的等前一笔提交后按最新的前缀最大号重算；
    # 不同前缀互不影响。拿锁之后才第一次读库，本事务看到的是前一笔已提交的数据
    locks.hold_prefixes({prefix})
    _lock_row(
        "sn_prefix_counter",
        "prefix = %s",
        (prefix,),
        "INSERT IGNORE INTO sn_prefix_counter(prefix, last_seq_dec, seq_grow, created_by, created_at, updated_at) "
        "VALUES (%s, 0, 1, %s, %s, %s)",
        (prefix, cu.emp_no, now, now),
    )
    fresh = _plan(pi, plan["scope"], [tuple(k) for k in plan["keys"]], plan.get("seq"))
    if fresh["issues"] or fresh["prefix"] != prefix or not _same_plan(plan, fresh):
        raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
    shifted = bool(plan.get("shifted")) or _shifted(plan, fresh)
    keys = plan["keys"]
    plan.clear()
    plan.update({**fresh, "keys": keys, "shifted": shifted})
    # 任务行的号段、计划在事务结束时随状态一起写（_run）：事务中途不锁任务行，进度、取消另用短连接写它
    _lock_row(
        "sn_pi_counter",
        "pi_no = %s",
        (pi,),
        "INSERT IGNORE INTO sn_pi_counter(pi_no, last_seq_dec, generated_qty, updated_at) VALUES (%s,0,0,%s)",
        (pi, now),
    )
    if db.scalar("SELECT status FROM sn_gen_job WHERE id = %s", j["id"]) != RUNNING:
        raise biz(ErrorCode.CONFLICT_RETRY)
    if _cancel_requested(j["id"]):
        raise biz(ErrorCode.GEN_CANCELED)
    # 事务内按最新快照和计数表重新校验额度（锁计数行）；预演后物料行失效（改了失效条件或状态刷新）也算计划已变
    vrule = line_void.current()
    for x in plan["items"]:
        lc = _lock_row(
            "sn_line_count",
            "bill_no = %s AND seq = %s",
            (x["bill_no"], x["seq"]),
            "INSERT IGNORE INTO sn_line_count(bill_no, seq, material_code, own_generated, gen_count, alloc_count, "
            "updated_at) VALUES (%s,%s,%s,0,0,0,%s)",
            (x["bill_no"], x["seq"], x["material_code"], now),
        )
        line = db.one(
            "SELECT qty, material_code, biz_status, pick_status FROM sn_prd_mo WHERE bill_no = %s AND seq = %s",
            x["bill_no"],
            x["seq"],
        )
        if line is None or vrule.is_void(line) or orders.sn_qty(line) - int(lc["own_generated"]) < x["qty"]:
            raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
    # 规则版本落库并归属本 PI；同一次生成里规则相同的物料行共用同一版
    versions: dict[str, dict] = {}
    for x in plan["items"]:
        if x["rule_key"] in versions:
            continue
        same = [(y["bill_no"], y["seq"]) for y in plan["items"] if y["rule_key"] == x["rule_key"]]
        res = _resolved_of(pi, x)
        versions[x["rule_key"]] = rules.realize(cu, pi, res, same)
    # PI 登记的起始号只管这一次生成：生成后清掉（之后流水接续）
    db.exec("UPDATE sn_pi_rule SET seq_start = NULL WHERE pi_no = %s", pi)
    for no in sorted({x["bill_no"] for x in plan["items"]}):
        changes.ensure_ledger(no)
        # 写号之前先给台账加共享锁：订单变更正在改这张单的暂停时等它提交，之后改暂停的等这次生成提交，
        # 写完号按台账重算暂停标记时读到的就是当前值
        changes.lock_ledger_suspend(no)
    done = 0
    # 上限自动突破：超出规则位数的号挂到对应位数的突破版本上（同一次生成里同一版本、同一位数共用）
    grown: dict[tuple[int, int], dict] = {}

    def version_for(base: dict, seq: int) -> dict:
        w = codec.seq_width(seq, rules.spec_of(base))
        if w == base["seq_len"]:
            return base
        k = (base["id"], w)
        if k not in grown:
            grown[k] = rules.grow_version(cu, pi, base, w)
        return grown[k]

    for x in plan["items"]:
        base_v = versions[x["rule_key"]]
        for s, e in _chunks(item_segs(x)):
            # 号段已按位数切开（见 _plan），一块内位数相同
            v = version_for(base_v, s)
            spec = rules.spec_of(v)
            sns = [codec.format_sn(q, spec, x["length"], x.get("unit")) for q in range(s, e + 1)]
            rows = [
                (
                    sn,
                    pi,
                    x["customer_code"],
                    x["bill_no"],
                    x["seq"],
                    x["material_code"],
                    x["org_code"],
                    x["bill_no"],
                    x["seq"],
                    pi,
                    s + i,
                    x["length"],
                    x["attach"],
                    v["id"],
                    spec.prefix,
                    j["gen_batch"],
                    now,
                    now,
                )
                for i, sn in enumerate(sns)
            ]
            try:
                db.exec_many(_INSERT_ITEM, rows)
            except pymysql.err.IntegrityError as ex:
                if not is_duplicate(ex):
                    raise
                # 撞上导入的号时优先报它
                hit = db.one(
                    f"SELECT sn, pi_no, origin FROM sn_item WHERE sn IN ({marks(len(sns))}) "
                    "ORDER BY origin = 'IMPORT' DESC LIMIT 1",
                    *sns,
                )
                if hit is None:
                    raise biz(ErrorCode.CONFLICT_RETRY) from None
                if hit["origin"] == util.ORIGIN_IMPORT:
                    raise biz(ErrorCode.GEN_IMPORTED_SN, sn=hit["sn"], pi=hit["pi_no"]) from None
                raise biz(ErrorCode.GEN_DUPLICATE_SN, sn=hit["sn"], pi=hit["pi_no"]) from None
            continue_deleted_rev(sns)
            bill_stats.touch(pi, x["bill_no"])
            done += len(sns)
            if _progress(j["id"], done, sns[-1]):
                raise biz(ErrorCode.GEN_CANCELED)
        db.exec(
            "UPDATE sn_line_count SET material_code = %s, own_generated = own_generated + %s, "
            "gen_count = gen_count + %s, updated_at = %s WHERE bill_no = %s AND seq = %s",
            x["material_code"],
            x["qty"],
            x["qty"],
            now,
            x["bill_no"],
            x["seq"],
        )
    # 前缀最大号只增不减：用空号段生成时可能不变；PI 统计记本 PI 自己生成的最大号与枚数
    db.exec(
        "UPDATE sn_prefix_counter SET last_seq_dec = GREATEST(last_seq_dec, %s), updated_at = %s WHERE prefix = %s",
        plan["new_last"],
        now,
        prefix,
    )
    db.exec(
        "UPDATE sn_pi_counter SET last_seq_dec = GREATEST(last_seq_dec, %s), generated_qty = generated_qty + %s, "
        "updated_at = %s WHERE pi_no = %s",
        plan["end_seq"],
        plan["qty"],
        now,
        pi,
    )
    changes.refresh_items({x["bill_no"] for x in plan["items"]}, quick=True)
    for no, items in _by_bill(plan["items"]).items():
        audit.record(
            cu,
            audit.entry(audit.SN_GENERATE)
            .factory(items[0]["org_code"])
            .pi(pi)
            .customer(items[0]["customer_code"])
            .bill(no)
            .range(items[0]["start_sn"], items[-1]["end_sn"])
            .count(sum(x["qty"] for x in items))
            .status(None, util.PENDING_ALLOC)
            .batch(j["gen_batch"])
            .info(
                f"job={j['id']} lines="
                + ",".join(f"{x['seq']}:{x['qty']}@{_segs_text(x)}[{versions[x['rule_key']]['id']}]" for x in items)
            ),
        )


def continue_deleted_rev(sns: list[str]) -> None:
    """刚写入号池的号（当前事务内）里，以前删过同样 SN 的：rev 接着删号存档里最大的 rev 加 1，不从 1 重来。
    删掉的号可能已交付 MES 且转移过（rev > 1），MES 按 rev 比较、小于本地的跳过（对接说明 3.2），
    rev 回到 1 会被当作迟到的旧数据跳过，回执照样 OK，本系统却以为 MES 已拿到新的挂靠。"""
    if not sns:
        return
    db.exec(
        "UPDATE sn_item i JOIN (SELECT sn, MAX(rev) r FROM sn_item_deleted "
        f"WHERE sn IN ({marks(len(sns))}) GROUP BY sn) d ON d.sn = i.sn SET i.rev = d.r + 1 "
        f"WHERE i.sn IN ({marks(len(sns))})",
        *sns,
        *sns,
    )


def _chunks(segs: list[list[int]]):
    """号段按 CHUNK 切块写入。"""
    for a, b in segs:
        s = a
        while s <= b:
            e = min(b, s + CHUNK - 1)
            yield s, e
            s = e + 1


def _segs_text(x: dict) -> str:
    """留痕：物料行的号段（多段用 ; 分隔）。"""
    return ";".join(f"{g['start_sn']}~{g['end_sn']}" for g in (x.get("seg_list") or _one_seg(x)))


def _resolved_of(pi: str, x: dict) -> rules.Resolved:
    """生成事务内重新解析该行规则，须与预演时一致。"""
    if x["rule_plan"] == rules.PLAN_NEW:
        return rules._custom_resolved(rules.SRC_LINE, x["custom"])
    rule = db.one("SELECT * FROM sn_rule WHERE id = %s FOR UPDATE", x["rule_id"])
    if rule is None:
        raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
    res = rules.resolve(rule, pi, rules.SRC_LINE)
    if res.version is None or res.version["id"] != x["version_id"]:
        raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
    return res


INTERRUPTED = "INTERRUPTED"


#: 定期回收中断生成任务的间隔（秒）：多实例下某个实例被杀（滚动发布、缩容、宕机）留下的任务不必等到下次重启
RECOVER_INTERVAL_SECONDS = 60


def recover_interrupted() -> int:
    """仍是 RUNNING、但执行它的进程已退出的任务（事务已随连接断开回滚）标为失败并释放 running_pi。

    生成从建任务到写完结果全程持有该 PI 的操作锁（MySQL 命名锁，进程退出、连接断开时服务端自动释放），
    所以拿得到这把锁就说明没有实例在执行它；拿不到（仍在执行，或该 PI 正有别的操作）留到下次。
    启动时与每 RECOVER_INTERVAL_SECONDS 秒各执行一次。
    """
    recovered = 0
    for j in db.all("SELECT id, pi_no FROM sn_gen_job WHERE status = %s", RUNNING):
        held = locks.try_acquire("pi", [j["pi_no"]])
        if held is None:
            continue
        try:
            with db.new_tx():
                row = db.one("SELECT status FROM sn_gen_job WHERE id = %s FOR UPDATE NOWAIT", j["id"])
                if row is None or row["status"] != RUNNING:
                    continue
                db.all("SELECT pi_no FROM sn_pi_counter WHERE pi_no = %s FOR UPDATE NOWAIT", j["pi_no"])
                db.exec(
                    "UPDATE sn_gen_job SET status = %s, done_qty = 0, error_code = %s, error_msg = %s, "
                    "running_pi = NULL, finished_at = %s WHERE id = %s",
                    FAILED,
                    INTERRUPTED,
                    "执行任务的服务已退出（重启、下线或宕机），任务未完成，已整批回滚，请重新预演后生成",
                    util.now(),
                    j["id"],
                )
                recovered += 1
        except pymysql.err.OperationalError as e:
            if error_no(e) != ER_LOCK_NOWAIT:
                raise
            log.info("gen_job_still_running", job=j["id"], pi=j["pi_no"])
        finally:
            held.release()
    if recovered:
        log.warning("gen_jobs_recovered", count=recovered)
    return recovered


def _progress(job_id: int, done: int, current: str) -> bool:
    """独立事务写进度（页面实时可见），并返回是否已请求取消。"""
    try:
        with db.new_tx():
            db.exec("UPDATE sn_gen_job SET done_qty = %s, current_sn = %s WHERE id = %s", done, current, job_id)
            return db.scalar("SELECT cancel_at FROM sn_gen_job WHERE id = %s", job_id) is not None
    except Exception:  # noqa: BLE001
        log.debug("progress_update_failed", job=job_id)
        return False


def _cancel_requested(job_id: int) -> bool:
    with db.new_tx():
        return db.scalar("SELECT cancel_at FROM sn_gen_job WHERE id = %s", job_id) is not None


# ================================================================== 进度 / 取消 / 撤销


def _job_by_batch(batch: str | None, lock: bool = False) -> dict:
    j = (
        None
        if util.blank(batch)
        else db.one("SELECT * FROM sn_gen_job WHERE gen_batch = %s" + (" FOR UPDATE" if lock else ""), util.trim(batch))
    )
    if j is None:
        raise biz(ErrorCode.GEN_JOB_NOT_FOUND)
    return j


def _orgs_of(items: list[dict]) -> set[str]:
    """生成计划中物料行的生产组织（早先的计划没有时按单据取）。"""
    out = {x["org_code"] for x in items if x.get("org_code")}
    out |= orders.bill_orgs(x["bill_no"] for x in items if not x.get("org_code"))
    return out


def in_scope(facs: tuple[str, ...] | None, x: dict) -> bool:
    """计划中的一个物料行号段是否在授权范围内（None = 全部）。"""
    return facs is None or (x.get("org_code") or "-") in facs


def progress(batch: str | None, cu: CurrentUser | None = None) -> dict:
    """进度：百分比、已生成 / 总数、已写到的号、速度、预计剩余时间。"""
    j = _job_by_batch(batch)
    if cu is not None:
        cu.check_read(*_orgs_of(json.loads(j["plan_json"])["items"]))
    out = job_json(j)
    elapsed = None
    if j["started_at"] is not None:
        end = j["finished_at"] or util.now()
        elapsed = max(1, int((end - j["started_at"]).total_seconds()))
    speed = None if not elapsed or not j["done_qty"] else j["done_qty"] / elapsed
    out["percent"] = 0 if not j["qty"] else min(100, int(j["done_qty"] * 100 / j["qty"]))
    out["speed_per_sec"] = None if speed is None else round(speed, 1)
    out["eta_seconds"] = (
        None if speed is None or j["status"] != RUNNING else int(max(0, j["qty"] - j["done_qty"]) / speed)
    )
    return out


def cancel(cu: CurrentUser, batch: str | None) -> dict:
    """取消生成中的任务：生成线程看到请求后整批回滚，任务记为已取消；取消留痕。"""
    with db.tx():
        j = _job_by_batch(batch, lock=True)
        cu.check_write(*_orgs_of(json.loads(j["plan_json"])["items"]))
        if j["status"] != RUNNING:
            raise biz(ErrorCode.GEN_CANCEL_NOT_RUNNING)
        if j["cancel_at"] is None:
            db.exec(
                "UPDATE sn_gen_job SET cancel_at = %s, cancel_by = %s WHERE id = %s", util.now(), cu.emp_no, j["id"]
            )
            audit.record(
                cu,
                audit.entry(audit.SN_GEN_CANCEL)
                .pi(j["pi_no"])
                .range(j["start_sn"], j["end_sn"])
                .count(j["qty"])
                .batch(j["gen_batch"])
                .status(RUNNING, CANCELED)
                .info(f"job={j['id']} done={j['done_qty']}/{j['qty']}"),
            )
    return progress(j["gen_batch"])


def jobs(pi: str | None, limit: int = 20, facs: tuple[str, ...] | None = None) -> list[dict]:
    """最近的生成任务。facs = 授权范围（None = 全部）：只列含范围内物料行的任务，任务里只列范围内的物料行。
    号的删除不改生成任务（任务是生成的历史记录；删号见 deletion）。"""
    pi = util.trim(pi)
    rows = db.all("SELECT * FROM sn_gen_job WHERE pi_no = %s ORDER BY id DESC LIMIT %s", pi, min(max(limit, 1), 200))
    out = []
    for j in rows:
        items = json.loads(j["plan_json"])["items"]
        if not any(in_scope(facs, x) for x in items):
            continue
        v = job_json(j)
        keep = {(x["bill_no"], x["seq"]) for x in items if in_scope(facs, x)}
        v["lines"] = [line for line in v["lines"] if (line["bill_no"], line["seq"]) in keep]
        out.append(v)
    return out
