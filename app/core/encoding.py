"""SN 编码：完整 SN = 前缀 + [前连接符 + 附件项次 + 后连接符] + 流水 + 米数前分隔符 + 米数 + 米数后分隔符 + 单位 + 后缀
（PRD 5.2.2）。

- 流水定长：左侧用字符表首字符（默认字符表即 0）补到流水位数；10 / 16 / 32 / 36 进制。
- 米数是字符串，取所属物料行的米数原样写入（只去首尾空格，000 / 001 / 0.5 / 其他都可以，不按数值规范化），
  不足米数补 0 位数时左侧补 0（超过不截断）；物料行米数为空时规则带米数不能生成（须先填写）。
  规则不带米数时米数和前后分隔符都不写。
- 单位取物料行的产品单位原样写入；规则不带单位时不写。
- 前缀、后缀、分隔符不限制字符（只去首尾空格）。前缀必填：一个前缀只归属一张 PI，同一 PI 内流水不重复，
  SN 就不重复（号池唯一约束兜底）。
- 附件项次（生成页自定义规则的组件，2026-11-24）：放在前缀与流水之间，取所属物料行的附件项次（与号上的 attach_no
  相同）原样写入（不补 0：1 就是 1，10 就是 10），前后各有一个连接符（attach_head / attach_tail，页面默认 -，都可以
  不填）。附件项次只能是数字（可带小数点，生成条件 ATTACH_NOT_NUMERIC 保证）：后连接符第一个字符不是数字、小数点时，
  前连接符之后连续的数字、小数点就是附件项次，项次长短不同也不撞号；后连接符不填（或以数字、小数点开头）时可能撞号
  （项次 1 + 流水 10001 与项次 11 + 流水 0001 都是 …110001），页面提示用户，撞上时号池唯一键拦下、整次生成回滚。
  前连接符不影响撞号（它前面是固定的前缀，前缀之间又不能一个是另一个的开头）。同一前缀下带不带附件项次、前后连接符
  锁定（见 rules.same_seq_format）。
- 流水在前缀（及附件项次段）之后、定长，所以按版本可以把 SN 反解出十进制流水。
- 上限自动突破（生成页自定义规则的开关）：流水用完规则位数后接着加位（4 位用到 9999 后 10000 起为 5 位，以此类推，
  最多 MAX_SEQ_LEN 位）；每个位数各是一个规则版本（同前缀、同格式，只有流水位数不同），号挂在自己位数的版本上，
  所以每个版本内流水仍是定长。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.core.errors import ErrorCode

#: 默认字符表（字母一律大写）；32 进制去掉易混的 I、L、O、U
DEFAULT_CHARSETS: dict[int, str] = {
    10: "0123456789",
    16: "0123456789ABCDEF",
    32: "0123456789ABCDEFGHJKMNPQRSTVWXYZ",
    36: "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
}
MAX_SEQ_LEN = 12
#: 米数补 0 位数上限（0 = 不补）
MAX_LENGTH_PAD = 32
#: 分隔符最多字符数
MAX_SEP = 16
#: 附件项次只能是数字（可带小数点）；后连接符第一个字符不是这些字符时项次与流水分得开（见文首）
_ATTACH_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_ATTACH_CHARS = "0123456789."
SN_MAX = 64
LONG_MAX = 2**63 - 1
#: 字符表不能含空白
_CHARSET_RE = re.compile(r"^\S+$")
#: 中文（含中文标点、全角字符）：页面填写的米数、产品单位不能含这些字符
_CHINESE_RE = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")


class SeqOverflow(ArithmeticError):
    """流水超出规则位数。"""


@dataclass(frozen=True)
class Spec:
    """规则版本的编码参数。charset 为完整字符表，长度 = 进制。"""

    prefix: str
    suffix: str
    base: int
    seq_len: int
    charset: str
    with_length: bool = True
    length_pad: int = 0
    sep_before: str = ""
    sep_after: str = ""
    with_unit: bool = False
    #: 带附件项次（前缀与流水之间，取物料行的附件项次原样写入）
    with_attach: bool = False
    #: 附件项次之后、流水之前的后连接符（可为空；不带附件项次时为空）
    attach_tail: str = ""
    #: 前缀与附件项次之间的前连接符（可为空；不带附件项次时为空）
    attach_head: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "prefix", self.prefix or "")
        object.__setattr__(self, "suffix", self.suffix or "")
        object.__setattr__(self, "sep_before", self.sep_before or "")
        object.__setattr__(self, "sep_after", self.sep_after or "")
        object.__setattr__(self, "length_pad", self.length_pad or 0)
        object.__setattr__(self, "with_attach", bool(self.with_attach))
        # 不带附件项次时连接符不起作用，统一为空（免得同一格式比较时不相等）
        object.__setattr__(self, "attach_tail", (self.attach_tail or "") if self.with_attach else "")
        object.__setattr__(self, "attach_head", (self.attach_head or "") if self.with_attach else "")

    @property
    def attach_safe(self) -> bool:
        """附件项次段能否从 SN 里唯一分出来：不带附件项次，或后连接符第一个字符不是数字、小数点（见文首）。"""
        return not self.with_attach or (self.attach_tail != "" and self.attach_tail[0] not in _ATTACH_CHARS)


def validate(s: Spec) -> ErrorCode | None:
    """校验参数；返回 None 表示合法，否则返回错误码。米数、单位部分在生成时按实际值再校验总长。"""
    if s.base not in DEFAULT_CHARSETS:
        return ErrorCode.RULE_BASE_INVALID
    if s.seq_len < 1 or s.seq_len > MAX_SEQ_LEN:
        return ErrorCode.RULE_SEQ_LEN_INVALID
    if not s.prefix:
        return ErrorCode.RULE_PREFIX_REQUIRED
    if s.length_pad < 0 or s.length_pad > MAX_LENGTH_PAD:
        return ErrorCode.RULE_LENGTH_PAD_INVALID
    if len(s.sep_before) > MAX_SEP or len(s.sep_after) > MAX_SEP:
        return ErrorCode.RULE_AFFIX_INVALID
    if len(s.attach_head) > MAX_SEP or len(s.attach_tail) > MAX_SEP:
        return ErrorCode.RULE_ATTACH_SEP_INVALID
    if s.charset is None or len(s.charset) != s.base:
        return ErrorCode.RULE_CHARSET_LENGTH
    if not _CHARSET_RE.match(s.charset):
        return ErrorCode.RULE_CHARSET_INVALID
    if len(set(s.charset)) != len(s.charset):
        return ErrorCode.RULE_CHARSET_DUPLICATE
    # 附件项次至少 1 位（实际长度在生成时按止号的 SN 长度再校验）
    head = len(s.prefix) + (len(s.attach_head) + 1 + len(s.attach_tail) if s.with_attach else 0)
    if head + len(s.suffix) + s.seq_len + _fixed_length_part(s) > SN_MAX:
        return ErrorCode.RULE_SN_TOO_LONG
    return None


def _fixed_length_part(s: Spec) -> int:
    """米数部分至少占的字符数（分隔符 + 补 0 位数）。"""
    return len(s.sep_before) + s.length_pad + len(s.sep_after) if s.with_length else 0


def max_seq(s: Spec) -> int:
    """该规则能表示的最大十进制流水（封顶在 long 上限）。"""
    v = s.base**s.seq_len
    return LONG_MAX if v - 1 > LONG_MAX else v - 1


def max_seq_grow(s: Spec) -> int:
    """上限自动突破时能表示的最大十进制流水：位数加到 MAX_SEQ_LEN 为止（封顶在 long 上限）。"""
    v = s.base**MAX_SEQ_LEN
    return LONG_MAX if v - 1 > LONG_MAX else v - 1


def seq_width(seq: int, s: Spec) -> int:
    """上限自动突破时这个流水用几位：不少于规则的流水位数，装不下时按实际位数（9999 之后 10000 为 5 位）。"""
    w = s.seq_len
    while w < MAX_SEQ_LEN and seq > s.base**w - 1:
        w += 1
    return w


def widen(s: Spec, width: int) -> Spec:
    """同一格式、流水位数换成 width（突破后的版本）。"""
    return s if width == s.seq_len else replace(s, seq_len=width)


def split_by_width(a: int, b: int, s: Spec) -> list[tuple[int, int, int]]:
    """号段 [a, b] 按突破后的位数切开：[(起, 止, 位数)]，每段内位数相同。"""
    out = []
    while a <= b:
        w = seq_width(a, s)
        top = s.base**w - 1 if w < MAX_SEQ_LEN else b
        e = min(b, top)
        out.append((a, e, w))
        a = e + 1
    return out


def encode_seq(seq: int, s: Spec) -> str:
    """定长进制流水；超出位数抛 SeqOverflow。"""
    if seq < 0:
        raise ValueError("negative seq")
    digits = [""] * s.seq_len
    n = seq
    for i in range(s.seq_len - 1, -1, -1):
        digits[i] = s.charset[n % s.base]
        n //= s.base
    if n != 0:
        raise SeqOverflow(f"seq {seq} exceeds {s.seq_len} digits of base {s.base}")
    return "".join(digits)


def decode_seq(text: str, s: Spec) -> int | None:
    index = {c: i for i, c in enumerate(s.charset)}
    n = 0
    for c in text:
        d = index.get(c)
        if d is None:
            return None
        n = n * s.base + d
    return n


def pad_length(length: str | None, s: Spec) -> str:
    """米数按规则左侧补 0 到补 0 位数（超过不截断）。"""
    v = length or ""
    return v.rjust(s.length_pad, "0") if s.length_pad else v


class AttachInvalid(ValueError):
    """附件项次为空或不是数字，不能写进带附件项次的规则。"""


def attach_fits(attach: str | None, s: Spec) -> bool:
    """附件项次能否写进规则（不带附件项次的规则都能）：去首尾空格后为数字（可带小数点）。"""
    if not s.with_attach:
        return True
    return _ATTACH_RE.match((attach or "").strip()) is not None


def attach_part(attach: str | None, s: Spec) -> str:
    """附件项次段：前连接符 + 附件项次原样（只去首尾空格，不补 0）+ 后连接符；规则不带附件项次时为空。
    不是数字抛 AttachInvalid。"""
    if not s.with_attach:
        return ""
    v = (attach or "").strip()
    if _ATTACH_RE.match(v) is None:
        raise AttachInvalid(f"attach {v!r} is not numeric")
    return s.attach_head + v + s.attach_tail


def format_sn(seq: int, s: Spec, length: str | None = None, unit: str | None = None, attach: str | None = None) -> str:
    """完整 SN；规则带附件项次时在前缀之后写入前连接符、附件项次（原样）、后连接符，
    带米数时写入（补 0）米数及前后分隔符，带单位时写入单位，不带时忽略。"""
    mid = s.sep_before + pad_length(length, s) + s.sep_after if s.with_length else ""
    tail = (unit or "") if s.with_unit else ""
    return s.prefix + attach_part(attach, s) + encode_seq(seq, s) + mid + tail + s.suffix


def head_ends(sn: str, s: Spec, attach: str | None = None) -> list[int]:
    """流水在 SN 里可能的起点：前缀之后；带附件项次时再跳过前连接符、附件项次、后连接符。
    - 给出 attach（号上记的附件项次）时按它定位，只有一个；
    - 不知道附件项次时：前连接符之后连续的数字、小数点里，后面紧跟后连接符的位置都算（后连接符第一个字符不是数字、
      小数点时只有一个；后连接符为空等情况可能有几个，调用方逐个试）。
    开头不是前缀、附件项次段对不上时为空。"""
    if not sn.startswith(s.prefix):
        return []
    i = len(s.prefix)
    if not s.with_attach:
        return [i]
    if not sn.startswith(s.attach_head, i):
        return []
    i += len(s.attach_head)
    if attach is not None:
        v = attach.strip()
        return [i + len(v) + len(s.attach_tail)] if v and sn.startswith(v + s.attach_tail, i) else []
    out = []
    j = i
    while j < len(sn) and sn[j] in _ATTACH_CHARS:
        j += 1
        if _ATTACH_RE.match(sn[i:j]) and sn.startswith(s.attach_tail, j):
            out.append(j + len(s.attach_tail))
    return out


def _parse_at(sn: str, h: int, s: Spec) -> int | None:
    if len(sn) - len(s.suffix) < h:
        return None
    body = sn[h : len(sn) - len(s.suffix)]
    if len(body) < s.seq_len:
        return None
    seq_text, rest = body[: s.seq_len], body[s.seq_len :]
    if s.with_length:
        if len(rest) < _fixed_length_part(s) or not rest.startswith(s.sep_before):
            return None
    elif not s.with_unit and rest:
        return None
    return decode_seq(seq_text, s)


def parse(sn: str | None, s: Spec, attach: str | None = None) -> int | None:
    """按规则反解完整 SN → 十进制流水；前缀、附件项次段、后缀、流水位数或字符、米数分隔符不符返回 None。
    attach：号上记的附件项次（知道时给出，带附件项次的规则按它定位流水）。"""
    if sn is None or not sn.endswith(s.suffix):
        return None
    for h in head_ends(sn, s, attach):
        n = _parse_at(sn, h, s)
        if n is not None:
            return n
    return None


def unit_of(sn: str | None, s: Spec, length: str | None = None, attach: str | None = None) -> str | None:
    """从完整 SN 截出写入的产品单位（米数传号上记的未补 0 原值，attach 传号上记的附件项次）；规则不带单位或号与规则
    对不上时返回 None。"""
    if not s.with_unit or sn is None or not sn.endswith(s.suffix):
        return None
    mid = s.sep_before + pad_length(length, s) + s.sep_after if s.with_length else ""
    end = len(sn) - len(s.suffix)
    for h in head_ends(sn, s, attach):
        head = h + s.seq_len + len(mid)
        if end >= head and sn[h + s.seq_len : head] == mid:
            return sn[head:end]
    return None


def clean_length(raw: str | None) -> str | None:
    """米数字符串：去首尾空格后原样使用；为空返回 None。"""
    v = (raw or "").strip()
    return v or None


def has_chinese(text: str | None) -> bool:
    """是否含中文（含中文标点、全角字符）。"""
    return bool(text) and _CHINESE_RE.search(text) is not None
