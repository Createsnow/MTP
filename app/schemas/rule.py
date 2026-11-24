from app.schemas.common import In


class RuleSpecIn(In):
    prefix: str | None = None
    suffix: str | None = None
    base: int | None = None
    seq_len: int | None = None
    charset: str | None = None
    #: 带米数；米数补 0 位数（0 = 不补）与前后分隔符只在带米数时起作用
    with_length: bool | None = True
    length_pad: int | None = 0
    sep_before: str | None = ""
    sep_after: str | None = ""
    #: 带单位（物料行的产品单位）
    with_unit: bool | None = False
    #: 附件项次（前缀与流水之间，取物料行的附件项次，左侧补 0 到这个位数；0 / 不传 = 不带）
    attach_width: int | None = 0
    #: 附件项次之后、流水之前的固定字符（不带附件项次时不起作用）
    attach_tail: str | None = ""


class RuleIn(RuleSpecIn):
    rule_code: str | None = None
    rule_name: str | None = None
    #: PI 选填（一条规则可给多张 PI 用）：都不填为未分类；
    #: 填了 category_scope 只能是 PI，category_value = PI 号（只用于推荐）
    category_scope: str | None = None
    category_value: str | None = None
    #: 所属生产组织：系统管理员不填 = 全局规则；普通操作员须是授权组织（只授权一个时可不填）
    factory_code: str | None = None


class RuleUpdateIn(RuleSpecIn):
    rule_name: str | None = None
    #: 都不传则分类与 PI 不变；传了只能是 PI，PI 必填
    category_scope: str | None = None
    category_value: str | None = None


class RulePreviewIn(RuleSpecIn):
    #: 示例米数（不填用 100）、示例单位（不填用 M）、示例附件项次（不填或比附件项次位数长时用 1）
    length: str | None = None
    unit: str | None = None
    attach: str | None = None
    #: 示例从这个流水（十进制）起（生成页自定义规则的起始号）；不填从 1 起
    seq_start: int | None = None
    #: 上限自动突破（生成页自定义规则的开关）：示例另带规则位数的最大号与突破后的第一个号；不传 = 关闭
    seq_grow: bool | None = None
    #: 识别码（生成页自定义规则；inc_end 不传 = 不带）：prefix 为前缀层文本，示例按 pi 将取的值（只预显示）
    pi_no: str | None = None
    inc_start: int | None = None
    inc_end: int | None = None
    inc_tail: str | None = None
    #: 识别码适用客户（页面上的清单；不传按已生效的，前缀还没有规则时为本 PI 的客户）、改选的已释放号段
    inc_customers: list[str] | None = None
    inc_pick: int | None = None


class LineRef(In):
    bill_no: str | None = None
    seq: int | None = None


class CustomRuleIn(In):
    """自定义规则：前缀必填（生成页默认取单据号后 7 位）；进制默认 10，流水位数默认 4，
    默认不带米数、不带单位，后缀选填；起始号选填。"""

    prefix: str | None = None
    base: int | None = None
    seq_len: int | None = None
    with_length: bool | None = False
    length_pad: int | None = 0
    sep_before: str | None = ""
    sep_after: str | None = ""
    with_unit: bool | None = False
    suffix: str | None = None
    #: 附件项次（前缀与流水之间；带识别码时在识别码及其固定字符之后）：生成时取物料行的附件项次，左侧补 0 到
    #: attach_width 位（项次比位数长的物料行不能生成）；attach_tail 为附件项次之后、流水之前的固定字符。
    #: 0 / 不传 = 不带（与原来相同）。同一前缀下附件项次位数与固定字符锁定
    attach_width: int | None = None
    attach_tail: str | None = None
    #: 起始号（十进制流水，选填）：首次用这条自定义规则生成、从 PI 最大号之后编号时，第一个号不小于它；
    #: 不是格式的一部分（同格式不同起始号仍是同一条规则），本 PI 已用过这个前缀时流水接续、不起作用
    seq_start: int | None = None
    #: 流水上限自动突破：开启时流水用完位数后自动加位（4 位用到 9999 后接 5 位 10000，以此类推，最多 12 位）；
    #: 不传或 false = 关闭（原逻辑：用完报规则位数已用尽）。不是格式的一部分，记在物料行上
    seq_grow: bool | None = None
    #: 识别码（中间件，紧跟前缀、在流水之前；inc_end 不传 = 不带）：prefix 为前缀层文本（防重只看它），
    #: 范围 inc_start~inc_end（0–999，位数为结束值的位数，不足补 0），inc_tail 为识别码之后、流水之前的固定字符。
    #: 适用客户下的 PI 依次取已用最大值 + 1，前缀 + 识别码为本 PI 独占的号段，流水从 1 开始
    inc_start: int | None = None
    inc_end: int | None = None
    inc_tail: str | None = None
    #: 识别码适用客户（只能选已同步的订单客户，须含本 PI 的客户；不传按已生效的清单）：前缀已有规则时，相对选规则时
    #: 已生效的清单「加了哪些、删了哪些」在本 PI 生成成功时才叠加到最新的清单上；前缀还没有规则时为第一次生成定下的清单
    inc_customers: list[str] | None = None
    #: 改选的已释放识别码号段（值）；不传 = 取下一个值
    inc_pick: int | None = None
    #: 规则编码（选填，字母、数字、_、-，最长 64 位；生成页默认 8 位时间哈希）：前缀是新的、生成成功存入规则库时
    #: 用它；不填按 8 位时间哈希；与已有编码相同时自动加 -v1、-v2…（不拦截）。前缀已有规则时用那条规则，不起作用
    rule_code: str | None = None


class LineSelectIn(In):
    pi_no: str | None = None
    lines: list[LineRef] | None = None
    #: 规则库规则（二选一）
    rule_id: int | None = None
    #: 自定义规则（二选一）
    custom: CustomRuleIn | None = None
    #: 2 = 第二 SN 的规则（双 SN 的 PI）；不填为第一 SN
    slot: int | None = None


class PiBindIn(In):
    pi_no: str | None = None
    rule_id: int | None = None
    #: 2 = 第二 SN 的规则（双 SN 的 PI）；不填为第一 SN
    slot: int | None = None


class IncCustomersIn(In):
    """识别码适用客户（规则库页面修改，保存即生效；查看影响时 pi_no 为生成页本 PI，它自己不算）。"""

    customers: list[str] | None = None
    pi_no: str | None = None


class PrefixGrowIn(In):
    """规则库按前缀改流水上限自动突破开关。"""

    prefix: str | None = None
    seq_grow: bool = True
