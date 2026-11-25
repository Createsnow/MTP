"""规则库与规则选取（F-08）：规则按 PI 维度绑定。
系统管理员、普通操作员（全局规则只能选用，授权组织的规则可增删改）。"""

from fastapi import APIRouter

from app.api.deps import OperatorDep
from app.modules.sn.services import rules
from app.schemas.rule import (
    IncCustomersIn,
    LineSelectIn,
    PiBindIn,
    PrefixGrowIn,
    RuleIn,
    RulePreviewIn,
    RuleSpecIn,
    RuleUpdateIn,
)

router = APIRouter(prefix="/rules", tags=["rules"])


def _spec(b: RuleSpecIn) -> rules.SpecIn:
    return rules.SpecIn(
        b.prefix,
        b.suffix,
        b.base,
        b.seq_len,
        b.charset,
        b.with_length,
        b.length_pad,
        b.sep_before,
        b.sep_after,
        b.with_unit,
        b.with_attach,
        b.attach_tail,
        b.attach_head,
    )


@router.get("", summary="规则库：可按分类与 PI 筛选，可按编码、名称、客户、PI 搜索")
def rule_list(
    cu: OperatorDep, q: str | None = None, category: str | None = None, category_value: str | None = None
) -> list:
    return rules.list_rules(q, category, category_value, cu.read_factories(None))


@router.get(
    "/prefixes", summary="规则库按前缀归档：一个前缀一组，组内为规则及使用它的 PI；可按前缀、编码、名称、PI 搜索"
)
def rule_prefixes(cu: OperatorDep, q: str | None = None, pi: str | None = None) -> list:
    return rules.prefix_archive(q, pi, cu.read_factories(None))


@router.get("/prefixes/usage", summary="前缀详情：用这个前缀的各 PI 占的流水与最大号")
def rule_prefix_usage(cu: OperatorDep, prefix: str | None = None) -> dict:
    return rules.prefix_usage(prefix, cu.read_factories(None))


@router.put("/prefixes/grow", summary="前缀的流水上限自动突破开关（作用于这个前缀下的全部 PI；已突破后不能关闭）")
def rule_prefix_grow(body: PrefixGrowIn, cu: OperatorDep) -> dict:
    return rules.set_prefix_grow(cu, body.prefix, body.seq_grow)


@router.post("", summary="新建规则：PI 选填（一条规则可给多张 PI 用，用在哪些 PI 由生成页选规则决定）")
def rule_create(body: RuleIn, cu: OperatorDep) -> dict:
    return rules.create(
        cu, body.rule_code, body.rule_name, _spec(body), body.category_scope, body.category_value, body.factory_code
    )


@router.put(
    "/{id:int}",
    summary="修改：从没生成过号的版本就地修改；生成过的改格式须另出一版并换新前缀；只改名称或 PI 不另出版；"
    "普通操作员只能改授权组织的规则",
)
def rule_update(id: int, body: RuleUpdateIn, cu: OperatorDep) -> dict:
    return rules.update(cu, id, body.rule_name, _spec(body), body.category_scope, body.category_value)


@router.put(
    "/{id:int}/inc-customers",
    summary="识别码适用客户（规则库页面，保存即生效）：删除的客户下号已全删掉的号段释放、还没生成的 PI 解除登记",
)
def rule_inc_customers(id: int, body: IncCustomersIn, cu: OperatorDep) -> dict:
    return rules.set_inc_customers(cu, id, body.customers)


@router.post("/{id:int}/inc-customers/impact", summary="改识别码适用客户前查看影响：受影响的 PI 与处理")
def rule_inc_customers_impact(id: int, body: IncCustomersIn, cu: OperatorDep) -> dict:
    return rules.inc_customers_impact(cu, id, body.customers, body.pi_no)


@router.delete(
    "/{id:int}", summary="删除：只能删从没生成过号、也没指定给任何 PI 的规则；普通操作员只能删授权组织的规则"
)
def rule_delete(id: int, cu: OperatorDep) -> dict:
    return rules.delete(cu, id)


@router.post(
    "/preview",
    summary="示例号：前 5 个和最大号（米数、单位、附件项次用示例值；可从起始号起；上限自动突破时另带突破后的第一个号）"
    "、最大流水",
)
def rule_preview(body: RulePreviewIn, cu: OperatorDep) -> dict:
    inc = None
    if body.inc_end is not None:
        inc = {
            "inc_start": body.inc_start,
            "inc_end": body.inc_end,
            "inc_tail": body.inc_tail,
            "inc_customers": body.inc_customers,
            "inc_pick": body.inc_pick,
        }
    return rules.preview(
        _spec(body), body.length, body.unit, body.seq_start, bool(body.seq_grow), inc, body.pi_no, body.attach
    )


@router.get("/options", summary="生成页规则库选项：推荐的排前面；给出本 PI 用这条规则时的版本与是否须换前缀")
def rule_options(cu: OperatorDep, pi: str | None = None, customer: str | None = None, slot: int | None = None) -> list:
    with rules.use_slot(slot or rules.SLOT1):
        return rules.options(pi, customer, cu.read_factories(None))


@router.post(
    "/line-select",
    summary="选定 / 改选本 PI 的规则（一个 PI 一个规则前缀）：规则库（rule_id）或自定义（前缀已有规则时归到它下面）",
)
def rule_line_select(body: LineSelectIn, cu: OperatorDep) -> dict:
    lines = [x.model_dump() for x in body.lines or []]
    custom = None if body.custom is None else body.custom.model_dump()
    with rules.use_slot(body.slot or rules.SLOT1):
        return rules.select_for_lines(cu, body.pi_no, lines, body.rule_id, custom)


@router.put("/pi-binding", summary="给 PI 登记规则库规则（已归属别的 PI 时共用前缀）")
def rule_pi_bind(body: PiBindIn, cu: OperatorDep) -> dict:
    with rules.use_slot(body.slot or rules.SLOT1):
        return rules.bind_pi(cu, body.pi_no, body.rule_id)


@router.delete("/pi-binding", summary="解除 PI 的规则登记（本 PI 有号时不能解除）")
def rule_pi_unbind(cu: OperatorDep, pi: str | None = None, slot: int | None = None) -> dict:
    with rules.use_slot(slot or rules.SLOT1):
        return rules.unbind_pi(cu, pi)
