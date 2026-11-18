"""SN 规则库与规则选取（F-08）。

以规则前缀为排号基准：同一前缀下各 PI 共用一套十进制流水（sn_prefix_counter 记前缀最大号，号上记 seq_prefix，
(seq_prefix, seq_dec) 唯一），排号、项次拦截见 generate。
- 一个 PI 一个规则前缀：选规则（select_for_lines / bind_pi）即登记在 PI 上（sn_pi_rule：前缀、规则库规则或自定义规则
  参数、起始号），本 PI 全部物料行、之后同步进来的单据都用它。本 PI 有号（生成过、没全删）时前缀锁定
  （RULE_PI_PREFIX_LOCKED），没号时可以改选；改选须对本 PI 全部单据的生产组织都有授权。
- 规则版本首次用于生成（或为某张 PI 换前缀另出、自定义规则生成）时归属该 PI。别的 PI 选同一条规则时默认共用前缀，
  生成时为它另出同前缀、同格式的版本（share_version，derived_from 记由哪一版而来）；也可以填新前缀另出版本（换前缀）。
- 同一前缀下进制、流水位数、字符表锁定（流水紧跟前缀、定长，前缀 + 流水才能唯一区分号）；米数（补 0、前后分隔符）、
  单位、后缀可各不相同：版本生成过号后改这几项，另出一版沿用同一前缀；改进制、位数或字符表须换新前缀。
  尚未归属 PI 的版本独占前缀，规则库保存时前缀不能与已有前缀相同。
- 任何两个不同的前缀都不能一个是另一个的开头（如 SD 与 SDD：流水不同也可能拼出相同的号）。
  万一拼出的完整 SN 相同，仍由号池唯一约束拦下、整次生成回滚。

自定义规则（前缀、进制、位数、米数及其补 0 位数与前后分隔符、是否带单位、后缀、起始号）：只登记在 PI 上，
生成成功后才存入规则库（编码 PI-PI号-n，名称 PI号_vn），PI 登记改记为这条规则；生成失败或取消不留规则。
与本 PI 已有的、或规则库里前缀、格式完全相同的规则归到那条规则下，不另建。起始号只管下一次生成（生成后清掉）。

流水上限自动突破按前缀统一设置（sn_prefix_counter.seq_grow，默认开启）：第一张 PI 选这个前缀时按这次的选择定下，
之后在规则库改（set_prefix_grow），已突破过规则位数后不能关闭。开启时流水用完规则位数后自动加位（最多 12 位），
每个位数各是一个版本（突破版本：derived_from 为原版本且前缀相同，归属本 PI，不改规则的当前版本）。

识别码（中间件，见 Inc）：SN = 前缀 + 识别码（+ 固定字符）+ 流水 + 米数 / 单位等 + 后缀。前缀防重只看前缀层
（inc_head，如 ABC）；前缀 + 自增值（如 ABC2）是一张 PI 独占的号段：版本的 prefix 存完整的 ABC2，排号、号池、删号、
自动突破、推送都按它走原逻辑，流水从 1 开始。同一前缀锁定第一次生成的 PI 的客户与自增位数；同一客户的 PI 依次取
已用最大值 + 1（选规则时只预显示，生成成功才占用；本 PI 已有的值删光号、改选后仍归它），超出范围须改前缀。
一个前缀一条规则：第一张 PI 生成时存入规则库（当前版本即它的配置），之后各 PI 的版本另出在这条规则下（不改当前版本）。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field, replace

from app.core import encoding as codec
from app.core import utils as util
from app.core.errors import ErrorCode, biz
from app.core.exceptions import BizError
from app.core.security import CurrentUser
from app.db.session import db, marks
from app.services import audit, factories, line_void, locks

#: 分类：新建、修改只能是 PI；NONE 未分类 / CUSTOMER 客户只出现在早先的规则上
NONE = "NONE"
CUSTOMER = "CUSTOMER"
PI = "PI"
_CODE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: 自定义规则的流水位数默认（规则库的默认值由页面给出）
CUSTOM_SEQ_LEN = 4

#: 规则来源（生效顺序）
SRC_LINE = "LINE"
SRC_PI = "PI"
#: 选法
MODE_LIBRARY = "LIBRARY"
MODE_CUSTOM = "CUSTOM"
#: 本 PI 用哪一版：USE 已归属本 PI / CLAIM 尚无 PI（首次生成时归属本 PI）/ NEW 自定义（生成时新建）/
#: SHARE 已归属别的 PI，共用前缀（生成时为本 PI 另出同前缀、同格式的版本，见 share_version）/
#: PREFIX 须为本 PI 填新前缀（早先的口径；规则库版本现在都可共用前缀，只在调用方保留）
PLAN_USE = "USE"
PLAN_CLAIM = "CLAIM"
PLAN_NEW = "NEW"
PLAN_SHARE = "SHARE"
PLAN_PREFIX = "PREFIX"
#: 带识别码的规则库规则，本 PI 还没有自己的值：生成时在这条规则下为本 PI 另出「前缀 + 自增值」的版本（见 Inc）
PLAN_INC = "INC"

RULE_FIELDS = (
    "id",
    "rule_code",
    "rule_name",
    "category_scope",
    "category_value",
    "factory_code",
    "current_version",
    "created_by",
    "created_at",
    "updated_by",
    "updated_at",
)
VERSION_FIELDS = (
    "id",
    "rule_id",
    "version",
    "prefix",
    "suffix",
    "base",
    "seq_len",
    "charset",
    "with_length",
    "length_pad",
    "length_sep_before",
    "length_sep_after",
    "with_unit",
    "used",
    "claimed_pi",
    "derived_from",
    "inc_head",
    "inc_tail",
    "inc_start",
    "inc_end",
    "inc_value",
    "inc_customer",
    "created_by",
    "created_at",
    "updated_at",
)

#: 识别码结束值上限（最多 3 位）
INC_MAX = 999


@dataclass(frozen=True)
class Inc:
    """识别码（中间件）：head 为前缀层文本（防重前缀），tail 为识别码之后、流水之前的固定字符；
    范围 start~end，位数为 end 的位数，不足左补 0（0~99 为 00~99）。"""

    head: str
    tail: str
    start: int
    end: int

    @property
    def width(self) -> int:
        return len(str(self.end))

    def text(self, value: int) -> str:
        return str(value).zfill(self.width)

    def prefix(self, value: int) -> str:
        """本 PI 号段的完整前缀（版本 prefix、排号、号池都按它）。"""
        return self.head + self.text(value) + self.tail

    def fields(self) -> dict:
        return {"inc_start": self.start, "inc_end": self.end, "inc_tail": self.tail}


def inc_of(v: dict | None) -> Inc | None:
    """版本行的识别码；不带为 None。"""
    if v is None or v.get("inc_head") is None:
        return None
    return Inc(v["inc_head"], v.get("inc_tail") or "", int(v["inc_start"]), int(v["inc_end"]))


def _inc_of_custom(c: dict, head: str) -> Inc | None:
    """自定义规则参数里的识别码（inc_end 不为空即带）：只能是数字，0 <= 起始值 <= 结束值 <= 999。"""
    if c.get("inc_end") is None:
        return None
    try:
        start = 0 if c.get("inc_start") is None else int(c["inc_start"])
        end = int(c["inc_end"])
    except (TypeError, ValueError):
        raise biz(ErrorCode.RULE_INC_RANGE_INVALID) from None
    if not 0 <= start <= end <= INC_MAX:
        raise biz(ErrorCode.RULE_INC_RANGE_INVALID)
    return Inc(head, util.trim(c.get("inc_tail")), start, end)


def rule_json(r: dict | None) -> dict | None:
    return util.jrow(r, RULE_FIELDS)


def version_json(v: dict | None) -> dict | None:
    if v is None:
        return None
    out = util.jrow(v, VERSION_FIELDS, ("used", "with_length", "with_unit"))
    out["format"] = format_text(spec_of(v))
    return out


def spec_of(v: dict) -> codec.Spec:
    return codec.Spec(
        v["prefix"],
        v["suffix"],
        v["base"],
        v["seq_len"],
        v["charset"],
        bool(v["with_length"]),
        int(v.get("length_pad") or 0),
        v.get("length_sep_before") or "",
        v.get("length_sep_after") or "",
        bool(v.get("with_unit")),
    )


def format_text(s: codec.Spec, inc: Inc | None = None) -> str:
    """格式说明：HX + 6 位 10 进制流水 + - + 米数（补足 5 位）+ M + 单位 + A。
    带识别码（inc）时前缀写成 HX + 识别码（0~9）（+ 固定字符）。"""
    head = [s.prefix] if inc is None else [inc.head, f"识别码（{inc.text(inc.start)}~{inc.text(inc.end)}）"]
    if inc is not None and inc.tail:
        head.append(inc.tail)
    parts = [*head, f"{s.seq_len} 位 {s.base} 进制流水"]
    if s.with_length:
        if s.sep_before:
            parts.append(s.sep_before)
        parts.append(f"米数（补足 {s.length_pad} 位）" if s.length_pad else "米数")
        if s.sep_after:
            parts.append(s.sep_after)
    if s.with_unit:
        parts.append("单位")
    if s.suffix:
        parts.append(s.suffix)
    return " + ".join(parts)


@dataclass(frozen=True)
class SpecIn:
    prefix: str | None
    suffix: str | None
    base: int | None
    seq_len: int | None
    charset: str | None
    with_length: bool | None = True
    length_pad: int | None = 0
    sep_before: str | None = ""
    sep_after: str | None = ""
    with_unit: bool | None = False


def normalize(s: SpecIn) -> codec.Spec:
    """规范化并校验编码参数；字符表留空用该进制默认字符表。前缀、后缀、分隔符不限制字符，只去首尾空格。"""
    if s.base is None or s.base not in codec.DEFAULT_CHARSETS:
        raise biz(ErrorCode.RULE_BASE_INVALID)
    if s.seq_len is None:
        raise biz(ErrorCode.RULE_SEQ_LEN_INVALID)
    cs = util.trim(s.charset) or codec.DEFAULT_CHARSETS[s.base]
    with_length = True if s.with_length is None else s.with_length
    spec = codec.Spec(
        util.trim(s.prefix),
        util.trim(s.suffix),
        s.base,
        s.seq_len,
        cs,
        with_length,
        # 不带米数时补 0 位数、分隔符都不起作用，统一存空，免得同一格式存成两样
        (s.length_pad or 0) if with_length else 0,
        util.trim(s.sep_before) if with_length else "",
        util.trim(s.sep_after) if with_length else "",
        bool(s.with_unit),
    )
    err = codec.validate(spec)
    if err is not None:
        raise biz(err)
    return spec


def prefix_taken(prefix: str, memo: dict[str, bool] | None = None) -> bool:
    """前缀是否已被某条规则的某个版本占用；memo：同一次判断里同一前缀只查一次。"""
    if memo is not None and prefix in memo:
        return memo[prefix]
    hit = db.exists("SELECT EXISTS(SELECT 1 FROM sn_rule_version WHERE prefix = %s)", prefix)
    if memo is not None:
        memo[prefix] = hit
    return hit


def same_seq_format(a: codec.Spec, b: codec.Spec) -> bool:
    """流水部分相同：进制、流水位数、字符表（同一前缀下锁定）。"""
    return (a.base, a.seq_len, a.charset) == (b.base, b.seq_len, b.charset)


def check_prefix(
    prefix: str,
    exclude_version: int | None = None,
    spec: codec.Spec | None = None,
    pi: str | None = None,
    gen: bool = False,
) -> None:
    """前缀能否给一个（归属 pi 的；pi = None 为尚未归属）版本用；区分大小写。
    - 已有同前缀的版本（不含 exclude_version）：都须已归属 PI（本 PI 或别的 PI，pi 非空），且进制、流水位数、字符表
      与 spec 相同，否则 RULE_PREFIX_CONFLICT（尚未归属的版本在用，或规则库保存时前缀已被用）/
      RULE_PREFIX_FORMAT_LOCKED（流水部分不同）。别的 PI 已用的前缀可共用：同一前缀下各 PI 共用一套流水
      （sn_prefix_counter、号上的 seq_prefix 唯一）。
    - 与任何别的前缀一个是另一个的开头：RULE_PREFIX_OVERLAP。
    在事务内调用：先拿这个前缀的前缀锁（锁到事务结束），并发占用同一前缀时后到的等前一个提交。
    规则库保存等短事务另对同前缀的版本加锁（FOR UPDATE，没有时锁住索引间隙）。
    gen（生成事务内）：不加 FOR UPDATE，按本事务所见与最新已提交的数据各判断一次。生成事务长（写几千到几十万个号），
    对还不存在的前缀 FOR UPDATE 锁住的索引间隙要到写完号才放；几张 PI 同时首次生成、各自新建前缀时，
    新前缀落在同一个间隙里，各自持有间隙锁又都要往里插入，成为死锁（报告第一轮 8 户 6 户失败即此）。"""
    locks.hold_prefix(prefix)
    if not gen:
        _prefix_rules(prefix, spec, pi, exclude_version, lock=True)
        return
    # 本事务里自己写过的（未提交）
    _prefix_rules(prefix, spec, pi, exclude_version, lock=False)
    # 最新已提交的：独立短事务里读（本事务的一致性快照可能早于别的生成提交；同前缀的提交已由前缀锁排在前面）
    with db.new_tx():
        _prefix_rules(prefix, spec, pi, exclude_version, lock=False)


def _prefix_rules(
    prefix: str, spec: codec.Spec | None, pi: str | None, exclude_version: int | None, lock: bool
) -> None:
    # 普通前缀不能与自增前缀（前缀层文本）相同或一个是另一个的开头：自增前缀下的全部号段（ABC0、ABC1…）都归它
    inc = _overlapping_inc(prefix)
    if inc is not None:
        raise biz(ErrorCode.RULE_PREFIX_OVERLAP, prefix=prefix, other=inc["inc_head"], pi=inc["claimed_pi"] or "-")
    every = db.all("SELECT * FROM sn_rule_version WHERE prefix = %s" + (" FOR UPDATE" if lock else ""), prefix)
    by_id = {v["id"]: v for v in every}
    for v in every:
        if v["id"] == exclude_version:
            continue
        # 尚未归属 PI 的版本独占前缀；规则库保存（pi 为空）不能与已有前缀相同。
        # 已归属别的 PI 的前缀可以共用（流水部分须相同，同前缀各 PI 共用一套流水，见 generate）
        if pi is None or v["claimed_pi"] is None:
            raise biz(ErrorCode.RULE_PREFIX_CONFLICT, prefix=prefix)
        # 突破版本（同前缀、由原版本加位而来）按原版本的流水格式比较
        b = by_id.get(v["derived_from"]) or v
        if spec is not None and not same_seq_format(spec_of(b), spec):
            raise biz(ErrorCode.RULE_PREFIX_FORMAT_LOCKED, prefix=prefix, base=b["base"], seq_len=b["seq_len"])
    for v in _overlapping(prefix):
        # 不论是否同一 PI：不同前缀一个是另一个的开头时，流水不同也可能拼出相同的号（如 P1+03+后缀 5 与 P103+5）
        raise biz(ErrorCode.RULE_PREFIX_OVERLAP, prefix=prefix, other=v["prefix"], pi=v["claimed_pi"] or "-")


def _overlapping(prefix: str) -> list[dict]:
    """与 prefix 一个是另一个开头的别的前缀（最多一行，取最早建的版本，与原来整表扫描时报的是同一个）。
    两头都走前缀索引 ix_rule_prefix，不随规则版本数变慢：以 prefix 开头的更长前缀按 LIKE 'prefix%' 取索引区间；
    是 prefix 开头的更短前缀就是 prefix 的各个真前缀，按等值查。"""
    heads = [prefix[:k] for k in range(1, len(prefix))]
    longer = "SELECT id, prefix, claimed_pi FROM sn_rule_version WHERE prefix LIKE %s AND prefix <> %s"
    shorter = f" UNION ALL SELECT id, prefix, claimed_pi FROM sn_rule_version WHERE prefix IN ({marks(len(heads))})"
    return db.all(
        "SELECT prefix, claimed_pi FROM (" + longer + (shorter if heads else "") + ") o ORDER BY id LIMIT 1",
        util.like_prefix(prefix),
        prefix,
        *heads,
    )


def _heads(text: str) -> list[str]:
    """text 的各个真前缀（开头部分）。"""
    return [text[:k] for k in range(1, len(text))]


def _overlapping_inc(prefix: str, other_than: str | None = None) -> dict | None:
    """与 prefix 相同或一个是另一个开头的自增前缀（inc_head；other_than 为自己的，不算）；最多一行。"""
    heads = _heads(prefix)
    sql = (
        "SELECT inc_head, claimed_pi FROM sn_rule_version WHERE inc_head IS NOT NULL"
        + ("" if other_than is None else " AND inc_head <> %s")
        + " AND (inc_head LIKE %s"
        + (f" OR inc_head IN ({marks(len(heads))})" if heads else "")
        + ") ORDER BY id LIMIT 1"
    )
    args = ([] if other_than is None else [other_than]) + [util.like_prefix(prefix), *heads]
    return db.one(sql, *args)


def _customer(pi: str) -> str | None:
    """PI 的客户编码（单据编号最小的那张单据的；一张 PI 只有一个客户编码）。"""
    from app.services import orders

    return orders.customer_of_pi(pi)


def inc_pick(pi: str, inc: Inc, customer: str | None) -> int:
    """本 PI 在自增前缀 inc.head 下用的自增值：本 PI 已有的（号全删掉、改选过也还是它的）；没有时为已用最大值 + 1
    （不小于起始值）。前缀已锁定别的客户（RULE_INC_CUSTOMER）、已按别的位数用了（RULE_INC_WIDTH）、
    超出范围（RULE_INC_EXHAUSTED，须改前缀）时报错。"""
    rows = db.all(
        "SELECT claimed_pi, inc_value, inc_end, inc_customer FROM sn_rule_version "
        "WHERE inc_head = %s AND inc_value IS NOT NULL",
        inc.head,
    )
    own = next((int(r["inc_value"]) for r in rows if r["claimed_pi"] == pi), None)
    if own is not None:
        return own
    if not customer:
        raise biz(ErrorCode.RULE_INC_NO_CUSTOMER, pi=pi)
    other = next((r["inc_customer"] for r in rows if r["inc_customer"] != customer), None)
    if other is not None:
        raise biz(ErrorCode.RULE_INC_CUSTOMER, prefix=inc.head, customer=other)
    width = next((len(str(r["inc_end"])) for r in rows if len(str(r["inc_end"])) != inc.width), None)
    if width is not None:
        raise biz(ErrorCode.RULE_INC_WIDTH, prefix=inc.head, width=width)
    value = max([inc.start] + [int(r["inc_value"]) + 1 for r in rows])
    if value > inc.end:
        raise biz(ErrorCode.RULE_INC_EXHAUSTED, prefix=inc.head, start=inc.text(inc.start), end=inc.text(inc.end))
    return value


def _inc_guard(inc: Inc, value: int, spec: codec.Spec, pi: str) -> None:
    """自增前缀的防重（只看前缀层 inc.head）：
    - 普通前缀与 head 相同或一个是另一个的开头、别的自增前缀与 head 一个是另一个的开头：RULE_PREFIX_OVERLAP；
    - 这个自增值已归别的 PI：RULE_INC_TAKEN（选规则后被别的 PI 先生成占走）；
    - 本 PI 的号段（完整前缀）已按别的进制 / 位数 / 字符表用过（号全删掉后仍锁定）：RULE_PREFIX_FORMAT_LOCKED。
    同一 head 下各值之间不互相校验：自增位数锁定、紧跟前缀，值不同的号在这一位上一定不同。"""
    head = inc.head
    heads = _heads(head)
    plain = db.one(
        "SELECT prefix, claimed_pi FROM sn_rule_version WHERE inc_head IS NULL AND (prefix LIKE %s"
        + (f" OR prefix IN ({marks(len(heads))})" if heads else "")
        + ") ORDER BY id LIMIT 1",
        util.like_prefix(head),
        *heads,
    )
    if plain is not None:
        raise biz(ErrorCode.RULE_PREFIX_OVERLAP, prefix=head, other=plain["prefix"], pi=plain["claimed_pi"] or "-")
    other = _overlapping_inc(head, other_than=head)
    if other is not None:
        raise biz(ErrorCode.RULE_PREFIX_OVERLAP, prefix=head, other=other["inc_head"], pi=other["claimed_pi"] or "-")
    taken = db.one(
        "SELECT claimed_pi FROM sn_rule_version WHERE inc_head = %s AND inc_value = %s AND claimed_pi <> %s LIMIT 1",
        head,
        value,
        pi,
    )
    if taken is not None:
        raise biz(ErrorCode.RULE_INC_TAKEN, prefix=head, value=inc.text(value), pi=taken["claimed_pi"])
    for v in db.all("SELECT * FROM sn_rule_version WHERE prefix = %s AND claimed_pi = %s", spec.prefix, pi):
        b = base_of(v)
        if not same_seq_format(spec_of(b), spec):
            raise biz(ErrorCode.RULE_PREFIX_FORMAT_LOCKED, prefix=spec.prefix, base=b["base"], seq_len=b["seq_len"])


def _inc_verify(inc: Inc, value: int, spec: codec.Spec, pi: str, customer: str | None) -> None:
    """本 PI 能否用自增值 value（完整前缀 spec.prefix）：防重、客户、位数、范围都过，且仍是本 PI 该取的值。"""
    _inc_guard(inc, value, spec, pi)
    got = inc_pick(pi, inc, customer)
    if got != value:
        raise biz(ErrorCode.RULE_INC_TAKEN, prefix=inc.head, value=inc.text(value), pi="-")


def _inc_lock_key(head: str) -> str:
    """自增前缀锁（与前缀锁同一组命名锁，键另加标记，不与真实前缀相同）。"""
    return "\x01INC\x01" + head


def check_inc(inc: Inc, value: int, spec: codec.Spec, pi: str, customer: str | None, gen: bool = False) -> None:
    """在事务内校验并占住自增前缀（拿自增前缀锁，锁到事务结束：同一自增前缀的取值、生成串行）。
    gen（生成事务内）：按本事务所见与最新已提交的数据各判断一次（本事务的快照可能早于别的 PI 的生成提交）。"""
    locks.hold_prefixes([_inc_lock_key(inc.head)])
    _inc_verify(inc, value, spec, pi, customer)
    if gen:
        with db.new_tx():
            _inc_verify(inc, value, spec, pi, customer)


def inc_usable(res: Resolved, pi: str) -> bool:
    """check_inc 的判断版（不加锁、不抛错）：带识别码的规则本 PI 现在能否用（生成页逐行判断）。"""
    if res.inc is None or res.version is None:
        return False
    try:
        _inc_verify(res.inc, int(res.version["inc_value"]), res.spec(), pi, res.version.get("inc_customer"))
    except BizError:
        return False
    return True


def check_line_prefix(res: Resolved | None, prefix: str | None, pi: str, registered: str | None = None) -> None:
    """确认生成时前缀不能用的具体原因（页面弹窗提示）：带识别码的规则按自增前缀判断（取不到值时为当时的原因；
    选规则时预显示的值（登记的前缀 registered）已被别的 PI 先生成占走时报 RULE_INC_TAKEN），其余按 check_prefix。"""
    if res is not None and res.fail is not None:
        raise res.fail
    if res is not None and res.inc is not None and res.version is not None:
        inc = res.inc
        if registered and registered != res.version["prefix"] and registered.startswith(inc.head):
            who = db.scalar(
                "SELECT claimed_pi FROM sn_rule_version WHERE prefix = %s AND claimed_pi <> %s LIMIT 1", registered, pi
            )
            raise biz(
                ErrorCode.RULE_INC_TAKEN,
                prefix=inc.head,
                value=registered[len(inc.head) : len(inc.head) + inc.width],
                pi=who or "-",
            )
        check_inc(inc, int(res.version["inc_value"]), res.spec(), pi, res.version.get("inc_customer"))
        return
    if prefix:
        check_prefix(prefix, spec=res.spec() if res is not None and res.ready else None, pi=pi)


def prefix_usable(prefix: str, spec: codec.Spec, pi: str, exclude_version: int | None = None) -> bool:
    """check_prefix 的判断版（不加锁、不抛错）：前缀能否给归属 pi、格式为 spec 的版本用。"""
    try:
        _prefix_rules(prefix, spec, pi, exclude_version, lock=False)
    except BizError:
        return False
    return True


def own_version(pi: str, spec: codec.Spec) -> Resolved | None:
    """自定义规则与本 PI 已有的版本完全同格式（如先生成的单据存入规则库的自定义规则）：同一 PI 的各张单据
    共用这一版，不再另建规则。没有完全同格式、且规则在本 PI 下正用这一版的版本时 None
    （前缀能否共用见 check_prefix）。"""
    for v in db.all("SELECT * FROM sn_rule_version WHERE prefix = %s AND claimed_pi = %s", spec.prefix, pi):
        if spec_of(v) != spec:
            continue
        r = db.one("SELECT * FROM sn_rule WHERE id = %s", v["rule_id"])
        if r is None:
            continue
        res = resolve(r, pi, SRC_LINE)
        # 规则在本 PI 下用的须正是这一版（规则另出过新版时不沿用，免得之后按新版生成）
        if res.version is not None and res.version["id"] == v["id"]:
            return res
    return None


def seq_label(seq: int, spec: codec.Spec, grow: bool = False) -> str:
    """流水号文本（留痕用）；上限自动突破时按突破后的位数。"""
    return codec.encode_seq(seq, codec.widen(spec, codec.seq_width(seq, spec)) if grow else spec)


def is_grow_version(v: dict, parent: dict | None) -> bool:
    """v 是否为 parent 上限自动突破另出的版本（derived_from 为 parent、前缀相同、流水位数不同）。
    换前缀另出的版本前缀不同；共用前缀另出的版本（别的 PI 用同一条规则，见 share_version）前缀、位数都相同。"""
    return (
        parent is not None
        and v.get("derived_from") == parent["id"]
        and v["prefix"] == parent["prefix"]
        and v["seq_len"] != parent["seq_len"]
    )


def base_of(v: dict) -> dict:
    """突破版本的原版本；不是突破版本时为它自己。"""
    if v.get("derived_from") is None:
        return v
    p = db.one("SELECT * FROM sn_rule_version WHERE id = %s", v["derived_from"])
    return p if is_grow_version(v, p) else v


def grow_version(cu: CurrentUser, pi: str, base: dict, width: int) -> dict:
    """生成事务内：版本 base 的流水上限自动突破到 width 位时用的版本。同规则、同前缀、同格式，只有流水位数为 width，
    归属本 PI、derived_from = base；已有则沿用，没有则另出一版（版本号接着排，不改规则的当前版本）。
    前缀已归属本 PI（base 已落库归属），不另查前缀、不另拿前缀锁（命名锁各占一条连接，同一事务再拿会等自己；
    同一 PI 的生成由 PI 操作锁串行）；先锁规则行，与规则库改版排队（版本号不撞）。"""
    if width == base["seq_len"]:
        return base
    db.one("SELECT id FROM sn_rule WHERE id = %s FOR UPDATE", base["rule_id"])
    v = db.one(
        "SELECT * FROM sn_rule_version WHERE rule_id = %s AND derived_from = %s AND prefix = %s AND seq_len = %s "
        "AND claimed_pi = %s",
        base["rule_id"],
        base["id"],
        base["prefix"],
        width,
        pi,
    )
    if v is not None:
        return v
    spec = codec.widen(spec_of(base), width)
    err = codec.validate(spec)
    if err is not None:
        raise biz(err)
    n = _next_version(base["rule_id"])
    vid = _insert_version(
        cu,
        base["rule_id"],
        n,
        spec,
        util.now(),
        used=True,
        claimed_pi=pi,
        derived_from=base["id"],
        inc=inc_of(base),
        inc_value=base.get("inc_value"),
        inc_customer=base.get("inc_customer"),
    )
    code = db.scalar("SELECT rule_code FROM sn_rule WHERE id = %s", base["rule_id"])
    audit.record(
        cu,
        audit.entry(audit.RULE_VERSION)
        .pi(pi)
        .info(
            f"{code} v{n} {describe(spec)} (v{base['version']} 流水用完 {base['seq_len']} 位，自动突破到 {width} 位)"
        ),
    )
    return db.one("SELECT * FROM sn_rule_version WHERE id = %s", vid)


def _pi_used_version(pi: str) -> dict | None:
    """本 PI 已生成、还在的号（流水归属本 PI，含已转到别的单据 / PI 的）里流水最小的那枚所在的版本；没有为 None。"""
    return db.one(
        "SELECT v.* FROM sn_item i JOIN sn_rule_version v ON v.id = i.rule_version_id "
        "WHERE i.seq_pi_no = %s AND i.origin = 'GEN' ORDER BY i.seq_dec LIMIT 1",
        pi,
    )


def pi_used_prefix(pi: str) -> str | None:
    """一个 PI 只用一个规则前缀：本 PI 已生成的号还在时为它们的前缀（之后选规则、生成都只能用这个前缀）；
    还没有号，或号已全部删掉时 None（可以改选）。"""
    v = _pi_used_version(pi)
    return None if v is None else v["prefix"]


def pi_prefix(pi: str) -> dict:
    """生成页「选规则」的默认前缀：prefix、custom（格式；没有为 None）、seq_grow（前缀的自动突破开关）、
    registered（本 PI 已登记规则）。
    - 本 PI 已登记规则（一个 PI 一个规则前缀）：为登记的前缀与格式，各单据都沿用；
    - 还没登记：custom 为 None，页面按默认规则取点选的第一个单据编码后 7 位；
      prefix 为 PI 第一张单据（单据编号最小）编码后 7 位，只在取不到点选的单据时兜底。"""
    row = pi_rule(pi)
    res = _registered(pi, row)
    if res is not None and res.inc is not None:
        # 已登记带识别码的规则：前缀为前缀层文本，custom 另带自增范围与本 PI 的值（取不到时 inc_error 为原因）
        assert row is not None
        v = None if res.version is None else (res.version if res.plan in (PLAN_NEW, PLAN_INC) else base_of(res.version))
        if v is not None:
            custom = _custom_dict(spec_of(v), res.inc)
        elif res.custom is not None:
            custom = dict(res.custom)
        else:
            # 规则库规则取不到值（已用完等）：按规则当前版本（第一张 PI 的配置）带出
            t = current_version(res.rule) if res.rule is not None else None
            custom = {} if t is None else _custom_dict(spec_of(t), res.inc)
        return _inc_prefix_info(res.inc, custom, v, res.fail, prefix_grow(row["prefix"]), True)
    if row is None or res is None or res.version is None:
        # 还没登记：本 PI 的客户用过带识别码的规则时，默认带出最近一次生成用的那条（下一个自增值）
        d = _customer_inc_default(pi)
        if d is not None:
            return d
        first = db.scalar("SELECT MIN(bill_no) FROM sn_prd_mo WHERE pi = %s", pi) or ""
        p = first[-7:]
        return {"prefix": p, "custom": None, "seq_grow": prefix_grow(p) if p else True, "registered": False}
    v = res.version if res.plan == PLAN_NEW else base_of(res.version)
    return {
        "prefix": row["prefix"],
        "custom": _custom_dict(spec_of(v)),
        "seq_grow": prefix_grow(row["prefix"]),
        "registered": True,
    }


def _inc_prefix_info(
    inc: Inc, custom: dict, v: dict | None, fail: BizError | None, grow: bool, registered: bool
) -> dict:
    """pi_prefix 的带识别码版：prefix 为前缀层文本（页面组件拆回为「前缀 + 识别码 + 固定字符」），
    inc_value 为本 PI 的值（只预显示），取不到时 inc_error 为原因（已用完须改前缀等）。"""
    value = None if v is None else v.get("inc_value")
    return {
        "prefix": inc.head,
        "custom": {**custom, "inc_value": value},
        "seq_grow": grow,
        "registered": registered,
        "inc": _inc_json(inc, value),
        "inc_error": _err_json(fail),
    }


def _customer_inc_default(pi: str) -> dict | None:
    """本 PI 的客户最近一次生成用的带识别码的规则：同一客户的新 PI 默认带出它的配置（取下一个自增值）；
    没有为 None。"""
    customer = _customer(pi)
    if not customer:
        return None
    last = db.one(
        "SELECT * FROM sn_rule_version WHERE inc_customer = %s AND inc_head IS NOT NULL ORDER BY id DESC LIMIT 1",
        customer,
    )
    if last is None:
        return None
    v = base_of(last)
    inc = inc_of(v)
    assert inc is not None
    spec = spec_of(v)
    fail = None
    try:
        value = inc_pick(pi, inc, customer)
    except BizError as e:
        value, fail = None, e
    draft = None if value is None else {**v, "inc_value": value}
    return _inc_prefix_info(inc, _custom_dict(spec, inc), draft, fail, True, False)


def pi_prefix_locked(pi: str) -> dict[str, dict]:
    """本 PI 已占用的前缀 → 锁定的格式（同 pi_prefix 的 custom：本 PI 这个前缀最新的版本，不算上限自动突破另出的版本）。
    号全部删掉后前缀仍归属本 PI、进制和流水位数仍锁定（check_prefix）：生成页「选规则」前缀是其中之一时按这个格式，
    免得按默认位数保存时报 RULE_PREFIX_FORMAT_LOCKED。"""
    out: dict[str, dict] = {}
    for v in db.all(
        "SELECT v.* FROM sn_rule_version v LEFT JOIN sn_rule_version p ON p.id = v.derived_from "
        "AND p.prefix = v.prefix AND p.seq_len <> v.seq_len WHERE v.claimed_pi = %s AND p.id IS NULL ORDER BY v.id",
        pi,
    ):
        out[v["prefix"]] = _custom_dict(spec_of(v))
        inc = inc_of(v)
        if inc is not None:
            # 带识别码：页面组成里的前缀是前缀层文本，按它也能找到本 PI 号段锁定的格式
            out[inc.head] = _custom_dict(spec_of(v), inc)
    return out


def _adopt(cu: CurrentUser, rule: dict, pi: str, orgs: set[str]) -> None:
    """物料行沿用本 PI 已有的规则版本（own_version）：生成时自动存入的本 PI 自定义规则被别的生产组织的物料行共用时
    改为全局规则（F-08 规则所属组织）；其他规则须能选用。"""
    if rule["factory_code"] is None or orgs <= {rule["factory_code"]}:
        return
    if rule["category_scope"] == PI and rule["category_value"] == pi:
        db.exec("UPDATE sn_rule SET factory_code = NULL WHERE id = %s", rule["id"])
    else:
        check_visible(cu, rule)


#: 示例号用的米数、单位（页面不填时）
SAMPLE_LENGTH = "100"
SAMPLE_UNIT = "M"


def preview(
    s: SpecIn,
    length: str | None = None,
    unit: str | None = None,
    seq_start: int | None = None,
    grow: bool = False,
    inc_in: dict | None = None,
    pi_in: str | None = None,
) -> dict:
    """编辑时的示例号：前 5 个和最大号（米数、单位用示例值）、最大流水（规则位数的）。seq_start：从这个流水起
    （自定义规则的起始号），不填从 1 起。grow（上限自动突破）：起始号可超过规则位数（按突破后的位数），
    最大号之后另带突破后的第一个号（最大流水 + 1）。
    inc_in（识别码：inc_start、inc_end、inc_tail；s.prefix 为前缀层文本）：示例按 pi_in 将取的值（只预显示，
    不占用）；取不到值（已用完须改前缀、前缀已锁定别的客户等）时 inc_error 为原因，示例按起始值。"""
    spec = normalize(s)
    inc = None if inc_in is None else _inc_of_custom(inc_in, spec.prefix)
    inc_out = None
    if inc is not None:
        pi = util.trim(pi_in)
        fail = None
        value = inc.start
        try:
            if not pi:
                raise biz(ErrorCode.PI_REQUIRED)
            value = inc_pick(pi, inc, _customer(pi))
        except BizError as e:
            fail = e
        spec = replace(spec, prefix=inc.prefix(value))
        err = codec.validate(spec)
        if err is not None:
            raise biz(err)
        inc_out = {**(_inc_json(inc, None if fail else value) or {}), "error": _err_json(fail)}
    sample_len = codec.clean_length(length) or SAMPLE_LENGTH
    sample_unit = util.trim(unit) or SAMPLE_UNIT
    mx = codec.max_seq(spec)
    first = check_seq_start(seq_start, spec, grow) or 1
    seqs = list(range(first, min(first + 4, mx) + 1)) + ([mx] if mx > first + 4 else [])
    if grow:
        seqs = list(range(first, first + 5)) if first > mx else seqs + [mx + 1]
    seqs = [i for i in seqs if i <= (codec.max_seq_grow(spec) if grow else mx)]

    def text(i: int) -> codec.Spec:
        return codec.widen(spec, codec.seq_width(i, spec)) if grow else spec

    samples = [
        {
            "seq_dec": i,
            "seq_text": codec.encode_seq(i, text(i)),
            "sn": codec.format_sn(i, text(i), sample_len, sample_unit),
        }
        for i in seqs
    ]
    return {
        "max_seq": mx,
        "format": format_text(spec, inc),
        "sample_length": sample_len,
        "sample_unit": sample_unit,
        "samples": samples,
        "inc": inc_out,
    }


def describe(s: codec.Spec) -> str:
    return (
        f"prefix={s.prefix} suffix={s.suffix} base={s.base} len={s.seq_len} with_length={int(s.with_length)} "
        f"length_pad={s.length_pad} sep_before={s.sep_before} sep_after={s.sep_after} "
        f"with_unit={int(s.with_unit)} charset={s.charset}"
    )


# ================================================================== 规则库


def _versions_of(rule_id: int) -> list[dict]:
    return db.all("SELECT * FROM sn_rule_version WHERE rule_id = %s ORDER BY version DESC", rule_id)


def _versions_by_rule(rule_ids: list[int] | None = None) -> dict[int, list[dict]]:
    """各规则的全部版本（新版本在前）；rule_ids = None 为全部规则。"""
    sql, args = "SELECT * FROM sn_rule_version", []
    if rule_ids is not None:
        if not rule_ids:
            return {}
        sql += f" WHERE rule_id IN ({marks(len(rule_ids))})"
        args = rule_ids
    out: dict[int, list[dict]] = {}
    for v in db.all(sql + " ORDER BY rule_id, version DESC", *args):
        out.setdefault(v["rule_id"], []).append(v)
    return out


def _current_of(r: dict, vs: list[dict]) -> dict | None:
    return next((v for v in vs if v["version"] == r["current_version"]), None)


def current_version(r: dict) -> dict | None:
    return db.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = %s", r["id"], r["current_version"])


def _column_by_rule(sql: str, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if ids:
        for row in db.all(sql.format(marks(len(ids))), *ids):
            out.setdefault(row["rule_id"], []).append(row["v"])
    return out


def _views(rs: list[dict]) -> list[dict]:
    """规则库列表与详情：每条规则的版本、用过的 PI、PI 绑定（一次取齐，不逐条查）。"""
    ids = [r["id"] for r in rs]
    versions = _versions_by_rule(ids)
    pi_bind = _column_by_rule("SELECT rule_id, pi_no v FROM sn_pi_rule WHERE rule_id IN ({}) ORDER BY pi_no", ids)
    out = []
    for r in rs:
        vs = versions.get(r["id"], [])
        out.append(
            {
                "rule": rule_json(r),
                "current": version_json(_current_of(r, vs)),
                "versions": [version_json(v) for v in vs],
                "used": any(v["used"] for v in vs),
                "pis": sorted({v["claimed_pi"] for v in vs if v["claimed_pi"]}),
                "bindings": {"pi": pi_bind.get(r["id"], [])},
            }
        )
    return out


def view(r: dict) -> dict:
    return _views([r])[0]


def _visible_sql(facs: tuple[str, ...] | None) -> tuple[str, list]:
    """授权范围内能看到、能选用的规则：全局规则 + 范围内组织的规则（None = 全部）。"""
    if facs is None:
        return "", []
    fsql, fargs = factories.scope_sql(facs)
    return f" AND (factory_code IS NULL OR (1=1{fsql}))", fargs


def check_visible(cu: CurrentUser, r: dict) -> None:
    """选用规则：全局规则或授权组织的规则。"""
    if r["factory_code"] is not None and not cu.can_read(r["factory_code"]):
        raise biz(ErrorCode.FACTORY_FORBIDDEN, factory=cu.scope_text())


def check_editable(cu: CurrentUser, r: dict) -> None:
    """修改、删除规则：系统管理员不限；普通操作员只能改授权组织的规则，全局规则只能选用。"""
    if cu.is_admin:
        return
    if r["factory_code"] is None:
        raise biz(ErrorCode.RULE_GLOBAL_READONLY, rule_code=r["rule_code"])
    cu.check_write(r["factory_code"])


def _owner(cu: CurrentUser, factory_code: str | None) -> str | None:
    """新建规则的所属组织：系统管理员可不填（全局规则）；普通操作员须是授权组织，只授权一个时可不填。"""
    f = util.trim_or_none(factory_code)
    if f is not None:
        cu.check_write(f)
        return factories.must_get(f)["factory_code"]
    if cu.is_admin:
        return None
    if len(cu.factories) == 1:
        return cu.factories[0]
    raise biz(ErrorCode.RULE_FACTORY_REQUIRED)


def list_rules(
    q: str | None,
    category: str | None = None,
    category_value: str | None = None,
    facs: tuple[str, ...] | None = None,
) -> list[dict]:
    """规则库；facs = 授权范围（None = 全部）：只列全局规则和范围内组织的规则。"""
    sql = "SELECT * FROM sn_rule WHERE 1=1"
    vsql, args = _visible_sql(facs)
    sql += vsql
    if not util.blank(category):
        sql += " AND category_scope = %s"
        args.append(util.trim(category))
    if not util.blank(category_value):
        sql += " AND category_value = %s"
        args.append(util.trim(category_value))
    text = util.trim(q)
    if text:
        like = util.like_any(text)
        sql += (
            " AND (rule_code LIKE %s OR rule_name LIKE %s OR category_value LIKE %s OR id IN "
            "(SELECT rule_id FROM sn_rule_version WHERE claimed_pi LIKE %s))"
        )
        args += [like] * 4
    sql += " ORDER BY category_scope = %s DESC, category_value ASC, rule_code ASC"
    args.append(PI)
    return _views(db.all(sql, *args))


# ================================================================== 规则库按前缀归档


def _seq_text_of(seq: int, spec: codec.Spec) -> str:
    """流水号文本（超出规则位数时按上限自动突破后的位数）；0 为空串。"""
    if seq <= 0:
        return ""
    try:
        return codec.encode_seq(seq, codec.widen(spec, codec.seq_width(seq, spec)))
    except (codec.SeqOverflow, ValueError):
        return str(seq)


def _base_versions(vs: list[dict]) -> list[dict]:
    """一条规则的版本里去掉上限自动突破另出的（同前缀、只有流水位数不同；号挂在它们上，格式按原版本）。"""
    by_id = {v["id"]: v for v in vs}
    return [v for v in vs if not is_grow_version(v, by_id.get(v["derived_from"]))]


def prefix_archive(q: str | None = None, pi: str | None = None, facs: tuple[str, ...] | None = None) -> list[dict]:
    """规则库按前缀归档（只读）：一个前缀一组，组内是用这个前缀的规则，每条规则列出使用它的 PI。
    - 同一前缀下进制、流水位数、字符表锁定（check_prefix），放在组上；米数、单位、后缀在规则上（取该规则在这个前缀上
      最新的版本，不算上限自动突破另出的版本）。一条规则换过前缀时出现在每个用过的前缀下。
    - pis：版本已归属的 PI（生成过号，含共用前缀另出的版本）；selected_pis：已登记这条规则、还没生成的 PI
      （按 resolve 本 PI 将用这个前缀的）。
    - top_seq：前缀最大号（sn_prefix_counter；生成时只增，删前缀最后一段时按现存号退回）；用这个前缀的 PI 接在它之后。
      seq_grow：前缀的自动突破开关。
    facs = 授权范围（None = 全部）：只列全局规则和范围内组织的规则。q 匹配前缀、规则编码、名称、PI；pi 精确筛选。"""
    vsql, args = _visible_sql(facs)
    rs = db.all("SELECT * FROM sn_rule WHERE 1=1" + vsql + " ORDER BY rule_code", *args)
    ids = [r["id"] for r in rs]
    versions = _versions_by_rule(ids)
    picked: dict[int, set[str]] = {}
    if ids:
        m = marks(len(ids))
        for row in db.all(f"SELECT rule_id, pi_no FROM sn_pi_rule WHERE rule_id IN ({m})", *ids):
            picked.setdefault(row["rule_id"], set()).add(row["pi_no"])
    groups: dict[str, dict] = {}
    for r in rs:
        vs = versions.get(r["id"], [])
        cur = _current_of(r, vs)
        base = _base_versions(vs)
        # 已选未生成的 PI 将用哪个前缀（resolve：已归属本 PI 的版本、首次归属或共用前缀时为当前版本的前缀）
        will: dict[str, set[str]] = {}
        for p in picked.get(r["id"], ()):
            try:
                res = _resolve_in(r, vs, p, SRC_LINE)
            except BizError:
                continue
            if res.version is not None and res.version["claimed_pi"] != p:
                will.setdefault(_group_key(res.version), set()).add(p)
        for prefix in dict.fromkeys(_group_key(v) for v in base):
            on = [v for v in base if _group_key(v) == prefix]
            shown = max(on, key=lambda v: v["id"])
            claimed = {v["claimed_pi"] for v in vs if _group_key(v) == prefix and v["claimed_pi"]}
            g = groups.setdefault(
                prefix,
                {
                    "prefix": prefix,
                    "base": shown["base"],
                    "seq_len": shown["seq_len"],
                    "charset": shown["charset"],
                    "rules": [],
                    "pis": set(),
                    "selected_pis": set(),
                    "used": False,
                    "factories": set(),
                    "spec": spec_of(shown),
                    "last_at": shown["created_at"],
                    "inc": None,
                },
            )
            inc = inc_of(shown)
            if inc is not None:
                # 自增前缀：组上为前缀层文本、范围、锁定的客户与各 PI 的值（每张 PI 一个号段）
                g["inc"] = {
                    **(_inc_json(inc, None) or {}),
                    "customer": shown["inc_customer"],
                    "values": sorted(
                        {
                            (int(v["inc_value"]), v["claimed_pi"], v["prefix"])
                            for v in on
                            if v["claimed_pi"] and v["inc_value"] is not None
                        }
                    ),
                }
            g["rules"].append(
                {
                    "rule": rule_json(r),
                    "version": version_json(shown),
                    "current": version_json(cur),
                    # 规则的当前版本已换到别的前缀时为 False（编辑按当前版本）
                    "is_current_prefix": cur is not None and _group_key(cur) == prefix,
                    "used": any(v["used"] for v in vs if _group_key(v) == prefix),
                    "rule_used": any(v["used"] for v in vs),
                    "pis": sorted(claimed),
                    "selected_pis": sorted(will.get(prefix, set()) - claimed),
                    "bindings": {"pi": sorted(picked.get(r["id"], set()))},
                }
            )
            g["pis"] |= claimed
            g["selected_pis"] |= will.get(prefix, set())
            g["used"] = g["used"] or g["rules"][-1]["used"]
            g["factories"].add(r["factory_code"])
            g["last_at"] = max(g["last_at"], max(v["updated_at"] for v in on))
    text = util.trim(q)
    want = util.trim(pi)
    out = []
    for g in groups.values():
        g["selected_pis"] -= g["pis"]
        names = [g["prefix"], *g["pis"], *g["selected_pis"]]
        for x in g["rules"]:
            names += [x["rule"]["rule_code"], x["rule"]["rule_name"]]
        if text and not any(text.lower() in (n or "").lower() for n in names):
            continue
        if want and want not in g["pis"] and want not in g["selected_pis"]:
            continue
        out.append(g)
    counters: dict[str, dict] = {}
    names_ = sorted(g["prefix"] for g in out)
    if names_:
        counters = {
            row["prefix"]: row
            for row in db.all(
                f"SELECT prefix, last_seq_dec, seq_grow FROM sn_prefix_counter WHERE prefix IN ({marks(len(names_))})",
                *names_,
            )
        }
    res = []
    for g in sorted(out, key=lambda x: (x["last_at"], x["prefix"]), reverse=True):
        c = counters.get(g["prefix"])
        top = 0 if c is None else int(c["last_seq_dec"] or 0)
        spec = g["spec"]
        res.append(
            {
                "prefix": g["prefix"],
                "base": g["base"],
                "seq_len": g["seq_len"],
                "charset": g["charset"],
                "rules": g["rules"],
                "pis": sorted(g["pis"]),
                "selected_pis": sorted(g["selected_pis"]),
                "used": g["used"],
                # None = 全局规则
                "factories": sorted(g["factories"], key=lambda f: (f is not None, f or "")),
                "top_seq": top,
                "top_text": _seq_text_of(top, spec),
                # 流水上限自动突破（按前缀；前缀还没登记时默认开启）
                "seq_grow": True if c is None else bool(c["seq_grow"]),
                # 自增前缀：{head, start, end, width, customer, values: [{value, text, pi_no, prefix}]}；
                # 各 PI 的号段各自排号（最大号、自动突破按各号段），组上不给 top_seq
                "inc": None
                if g["inc"] is None
                else {
                    **g["inc"],
                    "values": [
                        {"value": v, "text": str(v).zfill(g["inc"]["width"]), "pi_no": p, "prefix": x}
                        for v, p, x in g["inc"]["values"]
                    ],
                },
            }
        )
    return res


def _group_key(v: dict) -> str:
    """规则库按前缀归档的组：带识别码的版本按前缀层文本（ABC0、ABC1… 都在 ABC 下），其余按前缀。"""
    return v["inc_head"] if v.get("inc_head") is not None else v["prefix"]


def prefix_usage(prefix_in: str | None, facs: tuple[str, ...] | None = None) -> dict:
    """前缀详情：用这个前缀的各 PI 占的流水（号池里流水归属该 PI、挂在这个前缀的版本上的号：最小、最大流水与枚数，
    含已转出的）、PI 自己生成的最大号与前缀最大号。前缀须有授权范围内能看到的规则。"""
    prefix = util.trim(prefix_in)
    vsql, args = _visible_sql(facs)
    # 自增前缀（前缀层文本）：组内是各 PI 的号段（ABC0、ABC1…），按 inc_head 取
    col = (
        "inc_head"
        if prefix and db.exists("SELECT EXISTS(SELECT 1 FROM sn_rule_version WHERE inc_head = %s)", prefix)
        else "prefix"
    )
    vs = db.all(
        f"SELECT v.* FROM sn_rule_version v JOIN sn_rule r ON r.id = v.rule_id WHERE v.{col} = %s" + vsql,
        prefix,
        *args,
    )
    if not prefix or not vs:
        raise biz(ErrorCode.RULE_NOT_FOUND)
    every = db.all(f"SELECT * FROM sn_rule_version WHERE {col} = %s ORDER BY id", prefix)
    base = next((v for v in _base_versions(every)), every[0])
    spec = spec_of(base)
    codes = {
        row["id"]: row["rule_code"]
        for row in db.all(
            f"SELECT id, rule_code FROM sn_rule WHERE id IN ({marks(len({v['rule_id'] for v in every}))})",
            *sorted({v["rule_id"] for v in every}),
        )
    }
    pis = sorted({v["claimed_pi"] for v in every if v["claimed_pi"]})
    out = []
    if pis:
        vids = [v["id"] for v in every]
        m = marks(len(pis))
        used = {
            row["seq_pi_no"]: row
            for row in db.all(
                f"SELECT seq_pi_no, MIN(seq_dec) lo, MAX(seq_dec) hi, COUNT(*) n FROM sn_item "
                f"WHERE seq_pi_no IN ({m}) AND rule_version_id IN ({marks(len(vids))}) GROUP BY seq_pi_no",
                *pis,
                *vids,
            )
        }
        tops = {
            row["pi_no"]: int(row["last_seq_dec"] or 0)
            for row in db.all(f"SELECT pi_no, last_seq_dec FROM sn_pi_counter WHERE pi_no IN ({m})", *pis)
        }
        for p in pis:
            u = used.get(p)
            lo, hi, n = (int(u["lo"]), int(u["hi"]), int(u["n"])) if u else (0, 0, 0)
            mine = next((v for v in every if v["claimed_pi"] == p), None)
            out.append(
                {
                    "pi_no": p,
                    # 自增前缀：本 PI 的号段（完整前缀）与自增值
                    "seq_prefix": None if mine is None else mine["prefix"],
                    "inc_value": None if mine is None else mine.get("inc_value"),
                    "rules": sorted({codes.get(v["rule_id"], "") for v in every if v["claimed_pi"] == p}),
                    "min_seq": lo,
                    "max_seq": hi,
                    "qty": n,
                    "min_text": _seq_text_of(lo, spec),
                    "max_text": _seq_text_of(hi, spec),
                    # 最小到最大流水之间有没有号的流水（删号、跳号、别的 PI 占用）
                    "has_gap": n > 0 and hi - lo + 1 > n,
                    "top_seq": tops.get(p, 0),
                }
            )
        out.sort(key=lambda x: (x["min_seq"] == 0, x["min_seq"], x["pi_no"]))
    return {
        "prefix": prefix,
        "base": spec.base,
        "seq_len": spec.seq_len,
        "charset": spec.charset,
        "pis": out,
        # 前缀最大号（删前缀最后一段时按现存号退回）与自动突破开关
        "top_seq": prefix_last(prefix),
        "top_text": _seq_text_of(prefix_last(prefix), spec),
        "seq_grow": prefix_grow(prefix),
        "inc": _inc_json(inc_of(base), None) if col == "inc_head" else None,
    }


def get_rule(rule_id: int | None, lock: bool = False) -> dict:
    r = (
        None
        if rule_id is None
        else db.one("SELECT * FROM sn_rule WHERE id = %s" + (" FOR UPDATE" if lock else ""), rule_id)
    )
    if r is None:
        raise biz(ErrorCode.RULE_NOT_FOUND)
    return r


def _check_category(category: str | None, value: str | None) -> tuple[str, str]:
    """规则只绑定 PI：分类不填按 PI，填了只能是 PI；PI 必填，不超过 64 位。"""
    cs = util.trim(category) or PI
    if cs != PI:
        raise biz(ErrorCode.RULE_CATEGORY_INVALID)
    cv = util.trim(value)
    if not cv or len(cv) > 64:
        raise biz(ErrorCode.RULE_CATEGORY_REQUIRED)
    return cs, cv


def _insert_version(
    cu: CurrentUser,
    rule_id: int,
    version: int,
    spec: codec.Spec,
    now,
    *,
    used=False,
    claimed_pi=None,
    derived_from=None,
    inc: Inc | None = None,
    inc_value: int | None = None,
    inc_customer: str | None = None,
) -> int:
    """inc：带识别码的版本（spec.prefix 为完整的「前缀 + 自增值 + 固定字符」，inc_value 为取到的值）。"""
    return db.insert(
        "INSERT INTO sn_rule_version(rule_id, version, prefix, suffix, base, seq_len, charset, with_length, "
        "length_pad, length_sep_before, length_sep_after, with_unit, used, claimed_pi, derived_from, inc_head, "
        "inc_tail, inc_start, inc_end, inc_value, inc_customer, created_by, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rule_id,
        version,
        spec.prefix,
        spec.suffix,
        spec.base,
        spec.seq_len,
        spec.charset,
        1 if spec.with_length else 0,
        spec.length_pad,
        spec.sep_before,
        spec.sep_after,
        1 if spec.with_unit else 0,
        1 if used else 0,
        claimed_pi,
        derived_from,
        None if inc is None else inc.head,
        "" if inc is None else inc.tail,
        None if inc is None else inc.start,
        None if inc is None else inc.end,
        None if inc is None else inc_value,
        None if inc is None else inc_customer,
        cu.emp_no,
        now,
        now,
    )


def _insert_rule(
    cu: CurrentUser,
    code: str,
    name: str,
    spec: codec.Spec,
    cat: tuple[str, str],
    note: str = "",
    claimed_pi: str | None = None,
    factory: str | None = None,
    inc: Inc | None = None,
    inc_value: int | None = None,
    inc_customer: str | None = None,
) -> int:
    now = util.now()
    rid = db.insert(
        "INSERT INTO sn_rule(rule_code, rule_name, category_scope, category_value, factory_code, current_version, "
        "created_by, created_at, updated_by, updated_at) VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s)",
        code,
        name,
        cat[0],
        cat[1],
        factory,
        cu.emp_no,
        now,
        cu.emp_no,
        now,
    )
    _insert_version(
        cu,
        rid,
        1,
        spec,
        now,
        used=claimed_pi is not None,
        claimed_pi=claimed_pi,
        inc=inc,
        inc_value=inc_value,
        inc_customer=inc_customer,
    )
    audit.record(
        cu,
        audit.entry(audit.RULE_CREATE)
        .pi(cat[1] if cat[0] == PI else claimed_pi)
        .customer(cat[1] if cat[0] == CUSTOMER else None)
        .factory(factory)
        .info(f"{code} v1 {describe(spec)}{note}"),
    )
    return rid


def create(
    cu: CurrentUser,
    code: str | None,
    name: str | None,
    s: SpecIn,
    category=None,
    category_value=None,
    factory_code: str | None = None,
) -> dict:
    """新建规则：PI（category_value）选填；factory_code = 所属生产组织（系统管理员可不填 = 全局规则，
    普通操作员须是授权组织）。"""
    with db.tx():
        owner = _owner(cu, factory_code)
        c = util.trim(code)
        if not _CODE.match(c):
            raise biz(ErrorCode.RULE_CODE_INVALID)
        n = util.trim(name)
        if not n or len(n) > 128:
            raise biz(ErrorCode.RULE_NAME_REQUIRED)
        # PI 选填（一条规则可给多张 PI 用，用在哪些 PI 由生成页选规则决定）：不填为未分类；
        # 填了仍记为 PI 分类（只用于推荐）
        cat = (
            (NONE, "")
            if util.blank(category) and util.blank(category_value)
            else _check_category(category, category_value)
        )
        spec = normalize(s)
        check_prefix(spec.prefix, spec=spec)
        if db.exists("SELECT EXISTS(SELECT 1 FROM sn_rule WHERE rule_code = %s)", c):
            raise biz(ErrorCode.RULE_CODE_EXISTS, rule_code=c)
        rid = _insert_rule(cu, c, n, spec, cat, factory=owner)
        return view(get_rule(rid))


def _next_version(rule_id: int) -> int:
    return int(db.scalar("SELECT COALESCE(MAX(version), 0) FROM sn_rule_version WHERE rule_id = %s", rule_id)) + 1


def update(cu: CurrentUser, rule_id: int, name: str | None, s: SpecIn, category=None, category_value=None) -> dict:
    """结果：UPDATED 就地修改（当前版本从没生成过号）/ NEW_VERSION 另出一版（须换新前缀）/ RENAMED /
    RECATEGORIZED（改 PI）/ UNCHANGED。分类与 PI 都不传则不变（早先未分类 / 客户分类的规则原样保留）。"""
    with db.tx():
        r = get_rule(rule_id, lock=True)
        check_editable(cu, r)
        n = util.trim(name)
        if not n or len(n) > 128:
            raise biz(ErrorCode.RULE_NAME_REQUIRED)
        cur = db.one(
            "SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = %s FOR UPDATE",
            r["id"],
            r["current_version"],
        )
        spec = normalize(s)
        cs, cv = (
            (r["category_scope"], r["category_value"])
            if category is None and category_value is None
            else _check_category(category, category_value)
        )
        before = spec_of(cur)
        action = "UNCHANGED"
        version = r["current_version"]
        now = util.now()
        if spec != before and db.exists(
            "SELECT EXISTS(SELECT 1 FROM sn_rule_version WHERE rule_id = %s AND inc_head IS NOT NULL)", r["id"]
        ):
            # 带识别码的规则：规则库只展示，编码格式在生成页「选规则」里改（改前缀即另一条规则）
            raise biz(ErrorCode.RULE_INC_READONLY, rule_code=r["rule_code"])
        if spec != before:
            if cur["used"]:
                # 生成过号：另出一版。沿用同一前缀（只改米数、单位、后缀）时仍归属原 PI，流水部分须相同；
                # 换新前缀时新版本尚未归属，首次生成时归属
                same_prefix = spec.prefix == before.prefix
                owner = cur["claimed_pi"] if same_prefix else None
                check_prefix(spec.prefix, spec=spec, pi=owner)
                version = _next_version(r["id"])
                _insert_version(cu, r["id"], version, spec, now, claimed_pi=owner)
                action = "NEW_VERSION"
                audit.record(
                    cu,
                    audit.entry(audit.RULE_VERSION).info(
                        f"{r['rule_code']} v{version} {describe(spec)} (was {describe(before)})"
                    ),
                )
            else:
                if spec.prefix != before.prefix or not same_seq_format(spec, before):
                    check_prefix(spec.prefix, cur["id"], spec, cur["claimed_pi"])
                db.exec(
                    "UPDATE sn_rule_version SET prefix = %s, suffix = %s, base = %s, seq_len = %s, charset = %s, "
                    "with_length = %s, length_pad = %s, length_sep_before = %s, length_sep_after = %s, "
                    "with_unit = %s, updated_at = %s WHERE id = %s",
                    spec.prefix,
                    spec.suffix,
                    spec.base,
                    spec.seq_len,
                    spec.charset,
                    1 if spec.with_length else 0,
                    spec.length_pad,
                    spec.sep_before,
                    spec.sep_after,
                    1 if spec.with_unit else 0,
                    now,
                    cur["id"],
                )
                _forget(cur["id"])
                action = "UPDATED"
                audit.record(
                    cu,
                    audit.entry(audit.RULE_UPDATE).info(
                        f"{r['rule_code']} v{cur['version']} {describe(spec)} (was {describe(before)})"
                    ),
                )
        if (cs, cv) != (r["category_scope"], r["category_value"]):
            audit.record(
                cu,
                audit.entry(audit.RULE_CATEGORY)
                .pi(cv if cs == PI else None)
                .customer(cv if cs == CUSTOMER else None)
                .info(f"{r['rule_code']} {r['category_scope']}:{r['category_value']} -> {cs}:{cv}"),
            )
            action = "RECATEGORIZED" if action == "UNCHANGED" else action
        if n != r["rule_name"]:
            audit.record(cu, audit.entry(audit.RULE_RENAME).info(f"{r['rule_code']} {r['rule_name']} -> {n}"))
            action = "RENAMED" if action == "UNCHANGED" else action
        db.exec(
            "UPDATE sn_rule SET rule_name = %s, category_scope = %s, category_value = %s, current_version = %s, "
            "updated_by = %s, updated_at = %s WHERE id = %s",
            n,
            cs,
            cv,
            version,
            cu.emp_no,
            now,
            r["id"],
        )
        return {"action": action, "rule": view(get_rule(r["id"]))}


def delete(cu: CurrentUser, rule_id: int) -> dict:
    """只能删从没生成过号、也没指定给任何 PI（PI 绑定、物料行选定、版本归属）的规则（连同全部版本）。"""
    with db.tx():
        r = get_rule(rule_id, lock=True)
        check_editable(cu, r)
        vs = db.all("SELECT * FROM sn_rule_version WHERE rule_id = %s FOR UPDATE", r["id"])
        if any(v["used"] for v in vs):
            raise biz(ErrorCode.RULE_IN_USE, rule_code=r["rule_code"])
        pi = db.scalar("SELECT pi_no FROM sn_pi_rule WHERE rule_id = %s LIMIT 1", r["id"]) or next(
            (v["claimed_pi"] for v in vs if v["claimed_pi"]), None
        )
        if pi is not None:
            raise biz(ErrorCode.RULE_ASSIGNED, rule_code=r["rule_code"], pi=pi)
        db.exec("DELETE FROM sn_rule_version WHERE rule_id = %s", r["id"])
        db.exec("DELETE FROM sn_rule WHERE id = %s", r["id"])
        for v in vs:
            _forget(v["id"])
        cur = next((v for v in vs if v["version"] == r["current_version"]), None)
        audit.record(
            cu,
            audit.entry(audit.RULE_DELETE).info(
                f"{r['rule_code']} {r['rule_name']} "
                f"v{r['current_version']}{' ' + describe(spec_of(cur)) if cur else ''}"
            ),
        )
        return {"id": r["id"], "rule_code": r["rule_code"]}


# ================================================================== 本 PI 用哪一版


@dataclass(frozen=True)
class Resolved:
    """某物料行（或 PI 默认）将用的规则。version 为 None 表示须为本 PI 换前缀（plan = PREFIX）；
    plan = SHARE 时 version 为别的 PI 的版本（格式模板），生成时为本 PI 另出同前缀的版本。"""

    source: str
    mode: str
    plan: str
    rule: dict | None
    version: dict | None
    #: 自定义规则的参数（plan = NEW）
    custom: dict | None = None
    #: plan = SHARE / PREFIX：当前版本已归属的 PI
    other_pi: str | None = None
    #: 起始号（十进制流水）：从前缀最大号之后编号时第一个号不小于它；不参与 key。登记在 PI 上（sn_pi_rule.seq_start），
    #: 生成后清掉
    seq_start: int | None = None
    #: 流水上限自动突破（按前缀，见 sn_prefix_counter.seq_grow）；不参与 key（同格式共用同一版）
    seq_grow: bool = False
    #: 带识别码：version 的 prefix 为本 PI 的完整前缀，inc_value / inc_customer 为取到的值、PI 的客户
    inc: Inc | None = None
    #: 带识别码但本 PI 取不到值（已用完、前缀已锁定别的客户、位数不同）：version 为 None，fail 为原因
    fail: BizError | None = field(default=None, compare=False)

    @property
    def ready(self) -> bool:
        return self.version is not None

    def spec(self) -> codec.Spec:
        assert self.version is not None
        return spec_of(self.version)

    def key(self) -> str:
        """同一次生成里规则相同的物料行共用同一版：自定义规则按参数、规则库按版本。"""
        if self.plan == PLAN_NEW:
            return "C:" + json.dumps(self.custom, sort_keys=True)
        return f"V:{self.version['id'] if self.version else None}"


def resolve(rule: dict, pi: str, source: str) -> Resolved:
    """规则 rule 在 PI 下用哪一版：当前版本尚无 PI 或已归属本 PI → 用它；已归属别的 PI → 用为本 PI 另出的版本
    （换前缀另出的，或共用前缀另出的），没有则共用前缀（SHARE：生成时为本 PI 另出同前缀的版本）。"""
    return _resolve_in(rule, _versions_of(rule["id"]), pi, source)


def _resolve_in(rule: dict, vs: list[dict], pi: str, source: str) -> Resolved:
    """同 resolve，版本已取出（vs 为该规则全部版本，新版本在前）。"""
    t = _current_of(rule, vs)
    if t is None:
        raise biz(ErrorCode.RULE_VERSION_NOT_FOUND)
    if t.get("inc_head") is not None:
        return _resolve_inc(rule, vs, t, pi, source)
    # 为本 PI 另出的版本：换前缀另出的（前缀不同）或共用前缀另出的（前缀、位数都相同）；
    # 同前缀、位数不同的是上限自动突破另出的版本，不算
    own = next(
        (
            v
            for v in vs
            if v["claimed_pi"] == pi
            and v["id"] != t["id"]
            and not is_grow_version(v, t)
            and v["derived_from"] == t["id"]
        ),
        None,
    )
    if t["claimed_pi"] == pi:
        return Resolved(source, MODE_LIBRARY, PLAN_USE, rule, t)
    if own is not None:
        return Resolved(source, MODE_LIBRARY, PLAN_USE, rule, own)
    if t["claimed_pi"] is None:
        return Resolved(source, MODE_LIBRARY, PLAN_CLAIM, rule, t)
    return Resolved(source, MODE_LIBRARY, PLAN_SHARE, rule, t, other_pi=t["claimed_pi"])


def _own_inc(vs: list[dict], pi: str) -> dict | None:
    """本 PI 在带识别码的规则下自己的版本（最新的，不算上限自动突破另出的）；号全删掉、改选过也还是它的。"""
    by_id = {v["id"]: v for v in vs}
    return next(
        (
            v
            for v in vs
            if v["claimed_pi"] == pi
            and v.get("inc_head") is not None
            and not is_grow_version(v, by_id.get(v["derived_from"]))
        ),
        None,
    )


def _resolve_inc(rule: dict, vs: list[dict], t: dict, pi: str, source: str) -> Resolved:
    """带识别码的规则（当前版本 t 为第一张 PI 的配置）：本 PI 已有自己的值时用自己的版本；否则按 t 的配置取
    下一个自增值（PLAN_INC，只预显示，生成时才另出版本、占用）。取不到值时 version 为 None、fail 为原因。"""
    own = _own_inc(vs, pi)
    if own is not None:
        return Resolved(source, MODE_LIBRARY, PLAN_USE, rule, own, inc=inc_of(own))
    inc = inc_of(t)
    assert inc is not None
    customer = _customer(pi)
    try:
        value = inc_pick(pi, inc, customer)
    except BizError as e:
        return Resolved(source, MODE_LIBRARY, PLAN_INC, rule, None, inc=inc, fail=e)
    draft = {
        **t,
        "id": None,
        "prefix": inc.prefix(value),
        "used": 0,
        "claimed_pi": None,
        "derived_from": t["id"],
        "inc_value": value,
        "inc_customer": customer,
    }
    return Resolved(source, MODE_LIBRARY, PLAN_INC, rule, draft, inc=inc)


def _custom_spec(c: dict) -> codec.Spec:
    return normalize(
        SpecIn(
            c.get("prefix"),
            c.get("suffix"),
            c.get("base") or 10,
            c.get("seq_len") or CUSTOM_SEQ_LEN,
            None,
            c.get("with_length", False),
            c.get("length_pad") or 0,
            c.get("sep_before") or "",
            c.get("sep_after") or "",
            c.get("with_unit", False),
        )
    )


def check_seq_start(seq_start: int | None, spec: codec.Spec, grow: bool = False) -> int | None:
    """自定义规则的起始号：不填为 None；须为 1..规则最大流水（上限自动突破时为加到 12 位的最大流水）。"""
    if seq_start is None:
        return None
    mx = codec.max_seq_grow(spec) if grow else codec.max_seq(spec)
    if not 1 <= seq_start <= mx:
        raise biz(ErrorCode.RULE_SEQ_START_INVALID, max=mx)
    return int(seq_start)


def _custom_resolved(source: str, c: dict, pi: str | None = None) -> Resolved:
    """自定义规则参数 → 本 PI 将用的规则。带识别码（c 里 inc_end 不为空，prefix 为前缀层文本）时按 pi 取值：
    version 的 prefix 为完整的「前缀 + 自增值 + 固定字符」；取不到值时 version 为 None、fail 为原因。"""
    spec = _custom_spec(c)
    grow = bool(c.get("seq_grow"))
    start = check_seq_start(c.get("seq_start"), spec, grow)
    inc = _inc_of_custom(c, spec.prefix)
    custom = _custom_dict(spec, inc)
    extra: dict = {}
    if inc is not None:
        customer = _customer(pi) if pi else None
        try:
            if not pi:
                raise biz(ErrorCode.PI_REQUIRED)
            value = inc_pick(pi, inc, customer)
        except BizError as e:
            return Resolved(
                source,
                MODE_CUSTOM,
                PLAN_NEW,
                None,
                None,
                custom=custom,
                seq_start=start,
                seq_grow=grow,
                inc=inc,
                fail=e,
            )
        spec = replace(spec, prefix=inc.prefix(value))
        err = codec.validate(spec)
        if err is not None:
            raise biz(err)
        extra = {
            "inc_head": inc.head,
            "inc_tail": inc.tail,
            "inc_start": inc.start,
            "inc_end": inc.end,
            "inc_value": value,
            "inc_customer": customer,
        }
    draft = {
        "id": None,
        "rule_id": None,
        "version": 1,
        "prefix": spec.prefix,
        "suffix": spec.suffix,
        "base": spec.base,
        "seq_len": spec.seq_len,
        "charset": spec.charset,
        "with_length": 1 if spec.with_length else 0,
        "length_pad": spec.length_pad,
        "length_sep_before": spec.sep_before,
        "length_sep_after": spec.sep_after,
        "with_unit": 1 if spec.with_unit else 0,
        "used": 0,
        "claimed_pi": None,
        "derived_from": None,
        **extra,
    }
    return Resolved(source, MODE_CUSTOM, PLAN_NEW, None, draft, custom=custom, seq_start=start, seq_grow=grow, inc=inc)


def _custom_dict(spec: codec.Spec, inc: Inc | None = None) -> dict:
    """自定义规则参数（生成页「选规则」组件按它拆回）。带识别码时 prefix 为前缀层文本，另带 inc_start、inc_end、
    inc_tail（不带时与原来相同，没有这几项）。"""
    out = {
        "prefix": spec.prefix if inc is None else inc.head,
        "base": spec.base,
        "seq_len": spec.seq_len,
        "with_length": spec.with_length,
        "length_pad": spec.length_pad,
        "sep_before": spec.sep_before,
        "sep_after": spec.sep_after,
        "with_unit": spec.with_unit,
        "suffix": spec.suffix,
    }
    if inc is not None:
        out.update(inc.fields())
    return out


# ================================================================== PI 规则登记、规则前缀


def pi_rule(pi: str) -> dict | None:
    """PI 规则登记（sn_pi_rule）：前缀、规则库规则或自定义规则参数、起始号；没选过为 None。"""
    return db.one("SELECT * FROM sn_pi_rule WHERE pi_no = %s", pi)


def pi_rules(pis: list[str]) -> dict[str, dict]:
    if not pis:
        return {}
    return {r["pi_no"]: r for r in db.all(f"SELECT * FROM sn_pi_rule WHERE pi_no IN ({marks(len(pis))})", *pis)}


def prefix_row(prefix: str) -> dict | None:
    return db.one("SELECT * FROM sn_prefix_counter WHERE prefix = %s", prefix)


def prefix_grow(prefix: str) -> bool:
    """前缀的流水上限自动突破开关；前缀还没登记时按默认开启。"""
    v = db.scalar("SELECT seq_grow FROM sn_prefix_counter WHERE prefix = %s", prefix)
    return True if v is None else bool(v)


def prefix_last(prefix: str | None) -> int:
    """前缀最大流水（生成时只增；删号删到前缀最后一段时退回到现存号的最大流水，见 deletion）；没有为 0。"""
    if not prefix:
        return 0
    return int(db.scalar("SELECT last_seq_dec FROM sn_prefix_counter WHERE prefix = %s", prefix) or 0)


def ensure_prefix(cu: CurrentUser, prefix: str, grow: bool | None) -> None:
    """登记规则前缀（第一张 PI 选这个前缀时）：自动突破开关按这次的选择（不填默认开启）；已登记的不改。"""
    if db.exists("SELECT EXISTS(SELECT 1 FROM sn_prefix_counter WHERE prefix = %s)", prefix):
        return
    now = util.now()
    db.exec(
        "INSERT IGNORE INTO sn_prefix_counter(prefix, last_seq_dec, seq_grow, created_by, created_at, updated_at) "
        "VALUES (%s, 0, %s, %s, %s, %s)",
        prefix,
        0 if grow is False else 1,
        cu.emp_no,
        now,
        now,
    )


def set_prefix_grow(cu: CurrentUser, prefix_in: str | None, grow: bool) -> dict:
    """按前缀改流水上限自动突破开关（作用于这个前缀下的全部 PI）；已突破过规则位数（最大号超过原位数的最大流水）
    后不能关闭。前缀须已登记（有 PI 选过）或规则库里有用这个前缀的规则；有规则时须能修改其中一条。"""
    prefix = util.trim(prefix_in)
    with db.tx():
        locks.hold_prefix(prefix)
        vs = db.all("SELECT * FROM sn_rule_version WHERE prefix = %s ORDER BY id", prefix) if prefix else []
        regs = db.all("SELECT * FROM sn_pi_rule WHERE prefix = %s", prefix) if prefix else []
        if not vs and not regs:
            raise biz(ErrorCode.RULE_NOT_FOUND)
        if vs:
            rids = sorted({v["rule_id"] for v in vs})
            denied: BizError | None = None
            for r in db.all(f"SELECT * FROM sn_rule WHERE id IN ({marks(len(rids))})", *rids):
                try:
                    check_editable(cu, r)
                    break
                except BizError as e:
                    denied = e
            else:
                raise denied
            spec = spec_of(next(iter(_base_versions(vs)), vs[0]))
        else:
            for r in regs:
                _check_pi_scope(cu, r["pi_no"])
            spec = _custom_spec(json.loads(regs[0]["custom_json"] or "{}"))
        ensure_prefix(cu, prefix, True)
        if not grow and prefix_last(prefix) > codec.max_seq(spec):
            raise biz(ErrorCode.RULE_PREFIX_GROW_LOCKED, prefix=prefix)
        db.exec(
            "UPDATE sn_prefix_counter SET seq_grow = %s, updated_at = %s WHERE prefix = %s",
            1 if grow else 0,
            util.now(),
            prefix,
        )
        audit.record(cu, audit.entry(audit.RULE_UPDATE).info(f"prefix={prefix} seq_grow={int(grow)}"))
    return {"prefix": prefix, "seq_grow": grow}


def _registered(pi: str, row: dict | None) -> Resolved | None:
    """登记行 → 本 PI 将用的规则（起始号取登记的，自动突破取前缀的）；规则已删时 None。"""
    if row is None:
        return None
    if row["rule_id"] is None:
        res = _custom_resolved(SRC_PI, json.loads(row["custom_json"] or "{}"), pi)
    else:
        r = db.one("SELECT * FROM sn_rule WHERE id = %s", row["rule_id"])
        if r is None:
            return None
        res = resolve(r, pi, SRC_PI)
    start = None if row["seq_start"] is None else int(row["seq_start"])
    return replace(res, seq_start=start, seq_grow=prefix_grow(row["prefix"]))


def _inc_json(inc: Inc | None, value: int | None) -> dict | None:
    if inc is None:
        return None
    return {
        "head": inc.head,
        "tail": inc.tail,
        "start": inc.start,
        "end": inc.end,
        "width": inc.width,
        "value": value,
        "text": None if value is None else inc.text(int(value)),
        "prefix": None if value is None else inc.prefix(int(value)),
    }


def _err_json(e: BizError | None) -> dict | None:
    """页面按 code + params 渲染（同接口错误体）。"""
    return None if e is None else {"code": e.code, "message": e.message, "params": e.params}


def pi_default(pi: str) -> Resolved | None:
    """本 PI 的规则（一个 PI 一个规则，全部物料行都用它）；没选过时 None。"""
    return _registered(pi, pi_rule(pi))


def info(res: Resolved | None, length: str | None = None, start_seq: int = 1, unit: str | None = None) -> dict | None:
    """规则说明：来源、选法、规则、本 PI 用的版本、格式、第一个号（按本行米数、单位）。"""
    if res is None:
        return None
    out = {
        "source": res.source,
        "mode": res.mode,
        "plan": res.plan,
        "rule_id": None if res.rule is None else res.rule["id"],
        "rule_code": None if res.rule is None else res.rule["rule_code"],
        "rule_name": None if res.rule is None else res.rule["rule_name"],
        "version_id": None if res.version is None else res.version["id"],
        "version": None if res.version is None else res.version["version"],
        "other_pi": res.other_pi,
        "custom": res.custom,
        "seq_start": res.seq_start,
        "seq_grow": res.seq_grow,
        "format": None,
        "first_sn": None,
        "max_seq": None,
        # 识别码：前缀层文本、范围、本 PI 的值（只预显示的也给出）；取不到值时 inc_error 为原因
        "inc": _inc_json(res.inc, None if res.version is None else res.version.get("inc_value")),
        "inc_error": _err_json(res.fail),
    }
    if res.version is not None:
        s = res.spec()
        out["format"] = format_text(s)
        out["prefix"] = s.prefix
        out["base"] = s.base
        out["seq_len"] = s.seq_len
        out["with_length"] = s.with_length
        out["length_pad"] = s.length_pad
        out["sep_before"] = s.sep_before
        out["sep_after"] = s.sep_after
        out["with_unit"] = s.with_unit
        out["suffix"] = s.suffix
        # 上限自动突破时为加到 12 位的最大流水
        out["max_seq"] = codec.max_seq_grow(s) if res.seq_grow else codec.max_seq(s)
        if res.seq_start is not None:
            # 自定义规则的起始号：从最大号之后编号时第一个号不小于它
            start_seq = max(start_seq, res.seq_start)
        if (length is not None or not s.with_length) and 0 < start_seq <= out["max_seq"]:
            w = codec.widen(s, codec.seq_width(start_seq, s)) if res.seq_grow else s
            out["first_sn"] = codec.format_sn(start_seq, w, length, unit)
    elif res.rule is not None:
        t = current_version(res.rule)
        if t is not None:
            out["format"] = format_text(spec_of(t))
            out["template_prefix"] = t["prefix"]
    return out


def line_rules_brief(pi: str, keys: list[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """订单页右表的「SN 规则」列：一个 PI 一个规则，各物料行相同。"""
    if not keys or not pi:
        return {}
    v = info(pi_default(pi))
    return dict.fromkeys(keys, v)


# ================================================================== 选取 / 绑定


def _derive(cu: CurrentUser, rule: dict, pi: str, new_prefix: str | None) -> dict:
    """为本 PI 换前缀：按当前版本的格式另出属于本 PI 的版本。"""
    t = current_version(rule)
    p = util.trim(new_prefix)
    spec = replace(spec_of(t), prefix=p)
    err = codec.validate(spec)
    if err is not None:
        raise biz(err)
    check_prefix(p, spec=spec, pi=pi)
    n = _next_version(rule["id"])
    vid = _insert_version(cu, rule["id"], n, spec, util.now(), claimed_pi=pi, derived_from=t["id"])
    audit.record(
        cu,
        audit.entry(audit.RULE_PREFIX_VERSION)
        .pi(pi)
        .info(f"{rule['rule_code']} v{n} {describe(spec)} (由 v{t['version']} 换前缀，归属 {pi})"),
    )
    return db.one("SELECT * FROM sn_rule_version WHERE id = %s", vid)


def _resolve_for_select(cu: CurrentUser, rule: dict, pi: str, source: str, new_prefix: str | None) -> Resolved:
    """规则已归属别的 PI 时：填了新前缀则为本 PI 换前缀另出版本；不填则共用前缀（生成时另出同前缀的版本）。
    带识别码的规则：本 PI 没有自己的值时取下一个值（只预显示，生成时才另出版本），前缀防重按前缀层文本。"""
    res = resolve(rule, pi, source)
    if res.inc is not None:
        _check_inc_pre(rule, res, new_prefix)
        if res.plan == PLAN_INC:
            v = res.version
            check_inc(res.inc, int(v["inc_value"]), res.spec(), pi, v["inc_customer"])
        return res
    if res.plan in (PLAN_SHARE, PLAN_PREFIX):
        if util.blank(new_prefix):
            if res.plan == PLAN_PREFIX:
                t = current_version(rule)
                raise biz(
                    ErrorCode.RULE_PREFIX_CHANGE_REQUIRED,
                    rule_code=rule["rule_code"],
                    version=t["version"],
                    other_pi=res.other_pi,
                )
            check_prefix(res.version["prefix"], spec=res.spec(), pi=pi)
            return res
        _derive(cu, rule, pi, new_prefix)
        res = resolve(rule, pi, source)
    return res


def _check_inc_pre(rule: dict, pre: Resolved, new_prefix: str | None) -> None:
    """带识别码的规则库规则：取不到值时报原因（已用完、前缀已锁定别的客户等，须改前缀）；新前缀在「自定义」里改
    （改前缀即另一条规则，自增值从起始值开始）。"""
    if pre.inc is None:
        return
    if not util.blank(new_prefix):
        raise biz(ErrorCode.RULE_INC_PREFIX_CUSTOM, rule_code=rule["rule_code"])
    if pre.fail is not None:
        raise pre.fail


def _line_keys(pi: str, lines: list[dict] | None) -> list[tuple[str, int, dict]]:
    keys = []
    for x in lines or []:
        no = util.trim(x.get("bill_no"))
        seq = x.get("seq")
        line = db.one("SELECT * FROM sn_prd_mo WHERE bill_no = %s AND seq = %s", no, seq)
        if line is None:
            raise biz(ErrorCode.ORDER_LINE_NOT_FOUND, bill_no=no, seq=seq)
        if line["pi"] != pi:
            raise biz(ErrorCode.GEN_SCOPE_INVALID)
        keys.append((no, int(seq), line))
    if not keys:
        raise biz(ErrorCode.RULE_LINE_REQUIRED)
    # 失效的物料行（line_void）不能选规则
    vrule = line_void.current()
    bad = next(((no, seq) for no, seq, line in keys if vrule.is_void(line)), None)
    if bad is not None:
        raise biz(ErrorCode.LINE_VOID, bill_no=bad[0], seq=bad[1])
    return keys


def _row_ident(row: dict | None) -> tuple | None:
    """PI 登记的规则标识（判断是否改选）：规则库规则按规则 id，自定义规则按格式参数；没登记为 None。"""
    if row is None:
        return None
    if row["rule_id"] is not None:
        return ("R", int(row["rule_id"]))
    return ("C", json.dumps(json.loads(row["custom_json"] or "{}"), sort_keys=True))


def _pi_lines(pi: str) -> list[dict]:
    """本 PI 的全部有效物料行（失效的不选规则，见 line_void）。"""
    vrule = line_void.current()
    return [
        r for r in db.all("SELECT * FROM sn_prd_mo WHERE pi = %s ORDER BY bill_no, seq", pi) if not vrule.is_void(r)
    ]


def same_format_rule(cu: CurrentUser | None, pi: str, spec: codec.Spec) -> Resolved | None:
    """规则库里与 spec 前缀、格式完全相同的规则（本 PI 用它时将用的版本格式正是 spec）：自定义规则归到它下面，
    不再另建一条重复的规则（规则库按前缀归档）。cu 给出时只找能选用的规则（全局或授权组织的）；
    本 PI 已有的同格式版本见 own_version（先查它）。前缀已归属别的 PI 时为共用前缀（SHARE）。"""
    for v in db.all("SELECT * FROM sn_rule_version WHERE prefix = %s ORDER BY id", spec.prefix):
        if spec_of(v) != spec:
            continue
        r = db.one("SELECT * FROM sn_rule WHERE id = %s", v["rule_id"])
        if r is None or (cu is not None and r["factory_code"] is not None and not cu.can_read(r["factory_code"])):
            continue
        try:
            res = resolve(r, pi, SRC_LINE)
        except BizError:
            continue
        if res.version is not None and spec_of(res.version) == spec:
            return res
    return None


def _check_locked(pi: str, target: str | None) -> None:
    """一个 PI 一个规则前缀：本 PI 有号（生成过、没全删）时只能用号上的前缀；没有号时可以改选。"""
    locked = pi_used_prefix(pi)
    if locked is not None and target != locked:
        raise biz(ErrorCode.RULE_PI_PREFIX_LOCKED, pi=pi, prefix=locked, new=target or "-")


def _register(
    cu: CurrentUser,
    pi: str,
    res: Resolved,
    rule_id: int | None,
    custom: dict | None,
    seq_start: int | None,
    grow: bool | None,
    label: str,
) -> None:
    """登记本 PI 的规则（覆盖原登记；之后同步进来的单据也用它），前缀第一次被选时登记前缀与自动突破开关。
    改选（原来登记的是别的规则）作用于整张 PI，须对本 PI 全部单据的生产组织都有授权。"""
    prefix = res.version["prefix"]
    prev = db.one("SELECT * FROM sn_pi_rule WHERE pi_no = %s FOR UPDATE", pi)
    ident = ("R", int(rule_id)) if rule_id is not None else ("C", json.dumps(custom, sort_keys=True))
    if prev is not None and _row_ident(prev) != ident:
        _check_pi_scope(cu, pi)
    ensure_prefix(cu, prefix, grow)
    now = util.now()
    db.exec(
        "INSERT INTO sn_pi_rule(pi_no, prefix, rule_id, custom_json, seq_start, bound_by, bound_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE prefix = VALUES(prefix), rule_id = VALUES(rule_id), "
        "custom_json = VALUES(custom_json), seq_start = VALUES(seq_start), bound_by = VALUES(bound_by), "
        "bound_at = VALUES(bound_at)",
        pi,
        prefix,
        rule_id,
        None if custom is None else json.dumps(custom, ensure_ascii=False, sort_keys=True),
        seq_start,
        cu.emp_no,
        now,
    )
    audit.record(
        cu,
        audit.entry(audit.RULE_PI_BIND)
        .pi(pi)
        .info(label + ("" if prev is None else f" (was {prev['prefix']} rule_id={prev['rule_id'] or '-'})")),
    )


def select_for_lines(
    cu: CurrentUser,
    pi_in: str | None,
    lines: list[dict] | None,
    rule_id: int | None,
    custom: dict | None,
    new_prefix: str | None = None,
) -> dict:
    """选定 / 改选规则：一个 PI 一个规则前缀，登记在 PI 上，本 PI 全部物料行、之后同步进来的单据都用它。
    规则库（rule_id；版本已归属别的 PI 时默认共用前缀，填 new_prefix 则换前缀）或自定义（custom；与本 PI 已有的
    或规则库里前缀、格式完全相同的规则归到那条规则下；不同的生成成功后才存入规则库）。
    - lines：点选 / 勾选的物料行（须属本 PI、有写权限）。
    - 起始号（custom.seq_start）记在 PI 上，只管下一次生成。
    - 流水上限自动突破（custom.seq_grow）按前缀：前缀第一次被选时按这次的选择（不填默认开启），之后在规则库改。
    - 本 PI 有号时前缀锁定（RULE_PI_PREFIX_LOCKED），号全部删掉（或还没生成）可以改选。"""
    pi = util.trim(pi_in)
    if not pi:
        raise biz(ErrorCode.PI_REQUIRED)
    with db.tx():
        keys = _line_keys(pi, lines)
        cu.check_write(*{line["org_code"] for _, _, line in keys})
        grow = None
        if custom is not None:
            res = _custom_resolved(SRC_LINE, custom, pi)
            grow = custom.get("seq_grow")
            grow = None if grow is None else bool(grow)
            if res.fail is not None:
                raise res.fail
            _check_locked(pi, res.version["prefix"])
            start = res.seq_start
            if res.inc is not None:
                # 带识别码：只预显示本 PI 的值（生成时才占用，见 realize）；前缀防重按前缀层文本
                v = res.version
                check_inc(res.inc, int(v["inc_value"]), res.spec(), pi, v["inc_customer"])
                own = None
            else:
                own = own_version(pi, res.spec()) or same_format_rule(cu, pi, res.spec())
            if res.inc is not None:
                rid, cj = None, dict(res.custom)
                got = res.inc.text(int(res.version["inc_value"]))
                label = f"custom {format_text(res.spec())} inc={res.inc.head}[{got}]"
            elif own is not None:
                # 前缀、格式与本 PI 已有的版本或规则库里的规则完全相同：登记为那条规则
                _adopt(cu, own.rule, pi, {line["org_code"] for line in _pi_lines(pi)})
                if own.plan == PLAN_SHARE:
                    check_prefix(own.version["prefix"], spec=own.spec(), pi=pi)
                elif own.plan == PLAN_CLAIM:
                    check_prefix(own.version["prefix"], own.version["id"], own.spec(), pi)
                res = replace(own, seq_start=start)
                rid, cj = own.rule["id"], None
                label = f"{own.rule['rule_code']} v{own.version['version']}"
            else:
                check_prefix(res.version["prefix"], spec=res.spec(), pi=pi)
                rid, cj = None, dict(res.custom)
                label = f"custom {format_text(res.spec())}"
            label += "" if start is None else f" start={seq_label(start, res.spec(), True)}"
        else:
            rule = get_rule(rule_id)
            check_visible(cu, rule)
            pre = resolve(rule, pi, SRC_LINE)
            _check_inc_pre(rule, pre, new_prefix)
            target = (
                util.trim(new_prefix)
                if pre.plan in (PLAN_SHARE, PLAN_PREFIX) and not util.blank(new_prefix)
                else (pre.version["prefix"] if pre.version is not None else None)
            )
            _check_locked(pi, target)
            res = _resolve_for_select(cu, rule, pi, SRC_LINE, new_prefix)
            rid, cj, start = rule["id"], None, None
            label = f"{rule['rule_code']} v{res.version['version']}"
        _register(cu, pi, res, rid, cj, start, grow, label)
        res = replace(res, seq_grow=prefix_grow(res.version["prefix"]))
        return {"pi_no": pi, "rule": info(res), "lines": len(_pi_lines(pi))}


def _check_pi_scope(cu: CurrentUser, pi: str) -> None:
    """PI 级的规则影响这张 PI 的全部单据：普通操作员须对这张 PI 的全部单据的生产组织都有授权。"""
    if cu.is_admin:
        return
    orgs = set(db.column("SELECT DISTINCT org_code FROM sn_prd_mo WHERE pi = %s", pi))
    cu.check_write(*(orgs or {"-"}))


def bind_pi(cu: CurrentUser, pi_in: str | None, rule_id: int | None, new_prefix: str | None = None) -> dict:
    """给 PI 登记规则库规则（同生成页选规则，不带物料行）；版本已归属别的 PI 时默认共用前缀，填新前缀则换前缀。"""
    pi = util.trim(pi_in)
    if not pi:
        raise biz(ErrorCode.PI_REQUIRED)
    _check_pi_scope(cu, pi)
    with db.tx():
        rule = get_rule(rule_id)
        check_visible(cu, rule)
        pre = resolve(rule, pi, SRC_PI)
        _check_inc_pre(rule, pre, new_prefix)
        target = (
            util.trim(new_prefix)
            if pre.plan in (PLAN_SHARE, PLAN_PREFIX) and not util.blank(new_prefix)
            else (pre.version["prefix"] if pre.version is not None else None)
        )
        _check_locked(pi, target)
        res = _resolve_for_select(cu, rule, pi, SRC_PI, new_prefix)
        _register(cu, pi, res, rule["id"], None, None, None, f"{rule['rule_code']} v{res.version['version']}")
        return {"pi_no": pi, "rule": info(replace(res, seq_grow=prefix_grow(res.version["prefix"])))}


def unbind_pi(cu: CurrentUser, pi_in: str | None) -> dict:
    """解除 PI 的规则登记：本 PI 有号时不能解除（前缀锁定）。"""
    pi = util.trim(pi_in)
    _check_pi_scope(cu, pi)
    with db.tx():
        b = db.one("SELECT * FROM sn_pi_rule WHERE pi_no = %s FOR UPDATE", pi)
        if b is not None:
            used = pi_used_prefix(pi)
            if used is not None:
                raise biz(ErrorCode.RULE_PI_PREFIX_LOCKED, pi=pi, prefix=used, new="-")
            db.exec("DELETE FROM sn_pi_rule WHERE pi_no = %s", pi)
            audit.record(
                cu, audit.entry(audit.RULE_PI_UNBIND).pi(pi).info(f"{b['prefix']} rule_id={b['rule_id'] or '-'}")
            )
    return {"pi_no": pi}


def options(pi: str | None, customer: str | None, facs: tuple[str, ...] | None = None) -> list[dict]:
    """生成页规则库选项：本 PI 已用过的规则、分类与本 PI 或本 PI 客户相同的标「推荐」排前面
    （分类只用于推荐，不是绑定）；
    给出本 PI 用这条规则时的版本与处理（plan：SHARE 为共用别的 PI 的前缀，可另填新前缀换前缀）。
    facs = 授权范围（None = 全部）：只列全局规则和范围内组织的规则。"""
    out = []
    versions = _versions_by_rule()
    vsql, vargs = _visible_sql(facs)
    for r in db.all("SELECT * FROM sn_rule WHERE 1=1" + vsql + " ORDER BY rule_code", *vargs):
        vs = versions.get(r["id"], [])
        res = _resolve_in(r, vs, pi, SRC_LINE) if pi else None
        # 推荐：本 PI 已用过的规则（有版本归属本 PI），以及早先分类为本 PI / 本 PI 客户的规则
        recommended = (
            (pi and any(v["claimed_pi"] == pi for v in vs))
            or (r["category_scope"] == PI and r["category_value"] == pi)
            or (r["category_scope"] == CUSTOMER and customer and r["category_value"] == customer)
        )
        t = _current_of(r, vs)
        # 带识别码的规则锁定客户：本 PI 的客户正是它时推荐
        recommended = recommended or bool(t is not None and customer and t.get("inc_customer") == customer)
        item = {
            "rule_id": r["id"],
            "rule_code": r["rule_code"],
            "rule_name": r["rule_name"],
            "category_scope": r["category_scope"],
            "category_value": r["category_value"],
            "factory_code": r["factory_code"],
            "recommended": bool(recommended),
            "format": None if t is None else format_text(spec_of(t), inc_of(t)),
            # 本 PI 用这条规则时的版本号（共用前缀、须换前缀时为另出前的当前版本）
            "version": (res.version["version"] if res is not None and res.version is not None else None)
            or (None if t is None else t["version"]),
            "current_prefix": None if t is None else t["prefix"],
            "used_pis": len({v["claimed_pi"] for v in vs if v["claimed_pi"] is not None}),
            "plan": None if res is None else res.plan,
            "other_pi": None if res is None else res.other_pi,
            # 带识别码：本 PI 用它时的值与完整前缀（只预显示）；取不到值时 inc_error 为原因（已用完须改前缀等）
            "inc": _inc_json(
                inc_of(t) if res is None or res.inc is None else res.inc,
                None if res is None or res.version is None else res.version.get("inc_value"),
            ),
            "inc_error": None if res is None else _err_json(res.fail),
        }
        out.append(item)
    out.sort(key=lambda x: (not x["recommended"], x["rule_code"]))
    return out


# ================================================================== 生成事务内落库


_CODE_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _custom_names(pi: str) -> tuple[str, str]:
    """自定义规则：编码 PI-PI号-n，名称 PI号_vn（n 为本 PI 第几条自定义规则，两者一致）。"""
    stem = "PI-" + _CODE_UNSAFE.sub("_", pi)[:50] + "-"
    taken = set(db.column("SELECT rule_code FROM sn_rule WHERE rule_code LIKE %s", util.like_prefix(stem)))
    k = 1
    while f"{stem}{k}" in taken:
        k += 1
    return f"{stem}{k}", util.cut(f"{pi}_v{k}", 128)


def _bind_rule(pi: str, rule_id: int) -> None:
    """自定义规则存入规则库后：PI 登记改记为这条规则（前缀、起始号不变）。"""
    db.exec("UPDATE sn_pi_rule SET rule_id = %s, custom_json = NULL WHERE pi_no = %s", rule_id, pi)


def realize(cu: CurrentUser, pi: str, res: Resolved, lines: list[tuple[str, int]]) -> dict:
    """生成事务内：把本次要用的版本落库、标记已使用并归属本 PI，返回版本行。
    自定义规则（PLAN_NEW）这时才存入规则库（生成失败或取消不留规则），PI 登记改记为这条规则。
    与预演时的判断不一致（期间被别的 PI 先用、前缀被占用）时报预演已失效。
    带识别码的规则：这时才占用本 PI 的自增值（见 _realize_inc）。"""
    if res.fail is not None:
        raise res.fail
    if res.inc is not None and res.plan in (PLAN_NEW, PLAN_INC):
        return _realize_inc(cu, pi, res, lines)
    if res.plan == PLAN_NEW:
        spec = res.spec()
        err = codec.validate(spec)
        if err is not None:
            raise biz(err)
        from app.services import orders

        # 规则库里已有前缀、格式完全相同、已用于生成的规则（选定后别的 PI 先用同格式生成了）：归到那条规则下，不另建
        hit = same_format_rule(cu, pi, spec)
        if hit is not None and hit.plan in (PLAN_USE, PLAN_SHARE):
            v = realize(cu, pi, hit, lines)
            _bind_rule(pi, hit.rule["id"])
            return v
        check_prefix(spec.prefix, spec=spec, pi=pi, gen=True)
        code, name = _custom_names(pi)
        # 自定义规则归属本 PI 的生产组织（一个 PI 一个规则，全部物料行都用它；都在同一个组织时归该组织，
        # 否则为全局规则，各组织都能看到）
        orgs = {r["org_code"] for r in _pi_lines(pi)} | (orders.bill_orgs(no for no, _ in lines) - {"-"})
        owner = next(iter(orgs)) if len(orgs) == 1 else None
        rid = _insert_rule(
            cu, code, name, spec, (PI, pi), " (自定义规则，生成成功后存入规则库)", claimed_pi=pi, factory=owner
        )
        _bind_rule(pi, rid)
        return db.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = 1", rid)
    if res.version is None:
        raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
    if res.plan == PLAN_SHARE:
        return share_version(cu, pi, res.rule, res.version)
    if res.version["claimed_pi"] is None:
        # 首次归属本 PI：前缀不能与别的 PI 的前缀相同或一个是另一个的开头
        check_prefix(res.version["prefix"], res.version["id"], spec_of(res.version), pi, gen=True)
    n = db.exec(
        "UPDATE sn_rule_version SET used = 1, claimed_pi = %s WHERE id = %s AND (claimed_pi IS NULL OR claimed_pi = "
        "%s)",
        pi,
        res.version["id"],
        pi,
    )
    if n == 0 and not db.exists(
        "SELECT EXISTS(SELECT 1 FROM sn_rule_version WHERE id = %s AND claimed_pi = %s)", res.version["id"], pi
    ):
        raise biz(ErrorCode.GEN_PREVIEW_CHANGED)
    return db.one("SELECT * FROM sn_rule_version WHERE id = %s", res.version["id"])


def _realize_inc(cu: CurrentUser, pi: str, res: Resolved, lines: list[tuple[str, int]]) -> dict:
    """生成事务内：占用本 PI 的自增值，落库为本 PI 的版本（前缀为完整的「前缀 + 自增值 + 固定字符」）。
    先拿自增前缀锁，按最新已提交的数据再确认这个值（期间被别的 PI 先生成占走时报 RULE_INC_TAKEN）。
    一个前缀一条规则：这个自增前缀已有规则时，在它下面为本 PI 另出版本（不改当前版本，当前版本即第一张 PI 的配置）；
    还没有时（第一张 PI，自定义规则）存入规则库（编码 PI-PI号-n），当前版本即本 PI 的版本。PI 登记改记为这条规则。"""
    from app.services import orders

    inc = res.inc
    assert inc is not None and res.version is not None
    spec = res.spec()
    err = codec.validate(spec)
    if err is not None:
        raise biz(err)
    value = int(res.version["inc_value"])
    customer = res.version.get("inc_customer")
    check_inc(inc, value, spec, pi, customer, gen=True)
    orgs = {r["org_code"] for r in _pi_lines(pi)} | (orders.bill_orgs(no for no, _ in lines) - {"-"})
    fam = db.one("SELECT rule_id FROM sn_rule_version WHERE inc_head = %s ORDER BY id LIMIT 1", inc.head)
    if fam is None:
        code, name = _custom_names(pi)
        owner = next(iter(orgs)) if len(orgs) == 1 else None
        rid = _insert_rule(
            cu,
            code,
            name,
            spec,
            (PI, pi),
            f" (自定义规则，生成成功后存入规则库；前缀 {inc.head} 识别码取 {inc.text(value)}，锁定客户 {customer})",
            claimed_pi=pi,
            factory=owner,
            inc=inc,
            inc_value=value,
            inc_customer=customer,
        )
        _bind_rule(pi, rid)
        return db.one("SELECT * FROM sn_rule_version WHERE rule_id = %s AND version = 1", rid)
    rule = db.one("SELECT * FROM sn_rule WHERE id = %s FOR UPDATE", fam["rule_id"])
    # 本 PI 在这个前缀下已有完全同格式的版本（号全删掉后重选）：沿用，不另出
    for v in db.all(
        "SELECT * FROM sn_rule_version WHERE rule_id = %s AND claimed_pi = %s AND inc_head = %s ORDER BY id DESC",
        rule["id"],
        pi,
        inc.head,
    ):
        if spec_of(v) == spec and base_of(v)["id"] == v["id"]:
            _bind_rule(pi, rule["id"])
            return v
    t = current_version(rule)
    n = _next_version(rule["id"])
    vid = _insert_version(
        cu,
        rule["id"],
        n,
        spec,
        util.now(),
        used=True,
        claimed_pi=pi,
        derived_from=None if t is None else t["id"],
        inc=inc,
        inc_value=value,
        inc_customer=customer,
    )
    # 同一客户的各 PI 共用这条规则：别的生产组织的 PI 也用到时改为全局规则（各组织都能看到、选用）
    if rule["factory_code"] is not None and not orgs <= {rule["factory_code"]}:
        db.exec("UPDATE sn_rule SET factory_code = NULL WHERE id = %s", rule["id"])
    audit.record(
        cu,
        audit.entry(audit.RULE_PREFIX_VERSION)
        .pi(pi)
        .customer(customer)
        .info(f"{rule['rule_code']} v{n} {describe(spec)} (前缀 {inc.head} 识别码取 {inc.text(value)}，归属 {pi})"),
    )
    _bind_rule(pi, rule["id"])
    return db.one("SELECT * FROM sn_rule_version WHERE id = %s", vid)


def share_version(cu: CurrentUser, pi: str, rule: dict, t: dict) -> dict:
    """生成事务内：规则当前版本 t 已归属别的 PI，本 PI 共用它的前缀：另出同前缀、同格式、归属本 PI 的版本
    （derived_from = t；与上限自动突破另出的版本区分：位数相同）。已有则沿用。
    号不重靠流水：同一前缀下各 PI 共用一套流水，接在前缀最大号之后（generate）。"""
    db.one("SELECT id FROM sn_rule WHERE id = %s FOR UPDATE", rule["id"])
    v = db.one(
        "SELECT * FROM sn_rule_version WHERE rule_id = %s AND derived_from = %s AND prefix = %s AND seq_len = %s "
        "AND claimed_pi = %s",
        rule["id"],
        t["id"],
        t["prefix"],
        t["seq_len"],
        pi,
    )
    if v is not None:
        return v
    spec = spec_of(t)
    check_prefix(spec.prefix, spec=spec, pi=pi, gen=True)
    n = _next_version(rule["id"])
    vid = _insert_version(cu, rule["id"], n, spec, util.now(), used=True, claimed_pi=pi, derived_from=t["id"])
    audit.record(
        cu,
        audit.entry(audit.RULE_PREFIX_VERSION)
        .pi(pi)
        .info(
            f"{rule['rule_code']} v{n} {describe(spec)} "
            f"(共用 v{t['version']} 的前缀，同 PI {t['claimed_pi']}，归属 {pi})"
        ),
    )
    return db.one("SELECT * FROM sn_rule_version WHERE id = %s", vid)


# ================================================================== 进制流水显示


# 已被号引用的版本参数不可变，可放心缓存
_spec_cache: dict[int, codec.Spec | None] = {}
_cache_lock = threading.Lock()


def _forget(version_id: int) -> None:
    with _cache_lock:
        _spec_cache.pop(version_id, None)


def cached_spec(version_id: int | None) -> codec.Spec | None:
    # 0 = SN 导入的号（没有规则）：不查库
    if not version_id:
        return None
    with _cache_lock:
        if version_id in _spec_cache:
            return _spec_cache[version_id]
    v = db.one("SELECT * FROM sn_rule_version WHERE id = %s", version_id)
    s = None if v is None else spec_of(v)
    if s is not None:
        with _cache_lock:
            _spec_cache[version_id] = s
    return s


def seq_text(version_id: int | None, seq_dec: int | None) -> str:
    s = cached_spec(version_id)
    if s is None or seq_dec is None:
        return ""
    try:
        return codec.encode_seq(seq_dec, s)
    except (codec.SeqOverflow, ValueError):
        return ""
