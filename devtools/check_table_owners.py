"""表归属检查（docs/模块化单体改造方案.md 第 5.2 节、规则 R3 / R4）：每张表只有归属模块能写。

扫描 app/ 下 Python 代码里的 SQL 写语句（INSERT / REPLACE / UPDATE / DELETE / TRUNCATE），按「文件 → 模块」与
「表 → 模块」核对：
- 建表脚本里的每张表都要在 TABLE_OWNER 登记归属模块（新表必须登记，新模块的表须用模块前缀，如 box_、plt_）；
- 写表的每个文件都要能归到一个模块（FILE_MODULE，或 app/modules/<模块>/ 下按目录自动归属）；
- 跨模块写表只允许 EXCEPTIONS 里登记的（阶段 0 的现有违规，只许减、不许增）；登记了却已不再出现的例外也报错，
  提醒把它删掉。

用法（仓库根目录）：uv run python -m devtools.check_table_owners；有问题时退出码为 1。
CI 与 tests/test_boundaries.py 都会跑。
只认写在字符串里的表名；表名放在变量里拼出来的 SQL 检查不到（评审时注意）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SCHEMA = APP / "db" / "migration"

PLATFORM, ORG, ORDER, SN = "platform", "org", "order", "sn"

#: 新模块的表名前缀（规则 R4）；现有 sn_ 表不改名
MODULE_PREFIX = {"box": "box_", "pallet": "plt_"}

#: 表 → 归属模块（第 5.2 节）
TABLE_OWNER: dict[str, str] = {
    # 底座：操作痕迹、按天计数
    "sn_audit": PLATFORM,
    "sn_audit_archive": PLATFORM,
    "sn_daily_counter": PLATFORM,
    # 组织 / 账户
    "sn_factory": ORG,
    "sn_user": ORG,
    "sn_user_factory": ORG,
    # 工单：订单快照与同步、金蝶连接、单据台账、订单变更提醒、物料行补录
    "sn_prd_mo": ORDER,
    "sn_sync_state": ORDER,
    "sn_k3_config": ORDER,
    "sn_bill_ledger": ORDER,
    "sn_bill_ledger_line": ORDER,
    "sn_order_alert": ORDER,
    "sn_line_attach": ORDER,
    "sn_line_length": ORDER,
    "sn_line_unit": ORDER,
    # SN 管理：规则、生成、号池、导入、交付、转移、统计
    **{
        t: SN
        for t in (
            "sn_rule",
            "sn_rule_version",
            "sn_pi_rule",
            "sn_pi_rule2",
            "sn_pi_dual",
            "sn_prefix_counter",
            "sn_prefix_role",
            "sn_line_void_rule",
            "sn_line_count",
            "sn_pi_counter",
            "sn_gen_preview",
            "sn_gen_job",
            "sn_item",
            "sn_item_sub",
            "sn_item_deleted",
            "sn_item_sub_deleted",
            "sn_import_item",
            "sn_bill_stat",
            "sn_bill_stat_dirty",
            "sn_seq",
            "sn_rate_counter",
            "sn_delivery_batch",
            "sn_delivery_item",
            "sn_delivery_receipt",
            "sn_delivery_alert",
            "sn_push_task",
            "sn_bill_transfer",
            "sn_transfer",
            "sn_transfer_item",
        )
    },
}

#: 文件 → 模块（与 pyproject.toml 的 import-linter 契约一致）；目录搬迁后由 app/modules/<模块>/ 自动归属
FILE_MODULE: dict[str, str] = {
    **{f: PLATFORM for f in ("audit", "locks", "files", "partitions", "k3", "mes_client")},
    **{f: ORG for f in ("factories", "users")},
    **{f: ORDER for f in ("orders", "changes", "k3_config")},
    **{
        f: SN
        for f in (
            "rules",
            "generate",
            "workbench",
            "allocation",
            "delivery",
            "transfers",
            "query",
            "sn_export",
            "sn_import",
            "sn_sheet",
            "bill_stats",
            "dual",
            "seg",
            "line_void",
            "deletion",
        )
    },
    "audit_query": "log",
    "dashboard": "dashboard",
}

#: 不参与检查的文件：演示数据（只在 SN_DEMO_SEED=true 的空库上灌数据，按设计写所有模块的表）
EXEMPT = {"app/services/demo_seed.py"}

#: 阶段 0 登记的现有跨模块写入（2026-11-25）：(文件, 表) → 收口方式。阶段 2 收口后删除；新代码不得再加。
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("app/services/changes.py", "sn_item"): "订单变更改号的暂停标记 → order 发事件 BillSuspendChanged，SN 订阅",
    ("app/services/changes.py", "sn_daily_counter"): "按天计数写在订单模块 → 移到 platform.metrics.count_daily()",
    ("app/services/factories.py", "sn_item"): "组织开关 MES 时改号状态 → org 发事件 MesToggled，SN 订阅",
    ("app/services/factories.py", "sn_push_task"): "组织关 MES 时删推送任务 → 同上",
    ("app/services/delivery/push.py", "sn_factory"): "推送回写组织的配置状态 → 调 org.api.set_mes_cfg_status()",
    ("app/services/partitions.py", "sn_gen_preview"): "每日维护清理过期预演 → SN 模块注册自己的每日清理任务",
    ("app/services/partitions.py", "sn_rate_counter"): "每日维护清理 MES 拉取限流计数 → 同上",
    ("app/services/partitions.py", "sn_seq"): "每日维护清理批次号流水 → 同上",
}

_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+IGNORE)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|DELETE\s+\w+(?:\s*,\s*\w+)*\s+FROM"
    r"|TRUNCATE(?:\s+TABLE)?)\s+`?([a-z][a-z0-9_]*)`?",
    re.IGNORECASE,
)
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([a-z][a-z0-9_]*)`?", re.IGNORECASE)


def schema_tables() -> set[str]:
    out: set[str] = set()
    for f in sorted(SCHEMA.glob("*.sql")):
        out |= set(_CREATE.findall(f.read_text(encoding="utf-8")))
    return out


def module_of(rel: str) -> str | None:
    parts = rel.split("/")
    if parts[:2] == ["app", "modules"] and len(parts) > 2:
        return parts[2]
    if parts[:2] == ["app", "services"]:
        name = parts[2].removesuffix(".py")
        return FILE_MODULE.get(name)
    if parts[1] in ("core", "db", "middleware", "models"):
        return PLATFORM
    return None


def writes() -> dict[str, set[str]]:
    """文件（仓库相对路径）→ 它写的表。"""
    tables = schema_tables()
    out: dict[str, set[str]] = {}
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        found = {t for t in _WRITE.findall(p.read_text(encoding="utf-8")) if t in tables}
        if found:
            out[rel] = found
    return out


def check(found: dict[str, set[str]] | None = None, owners: dict[str, str] | None = None) -> list[str]:
    """found / owners 只给测试用：默认扫描 app/ 与 TABLE_OWNER。"""
    problems: list[str] = []
    tables = schema_tables()
    owner_map = TABLE_OWNER if owners is None else owners
    found = writes() if found is None else found
    for t in sorted(tables - owner_map.keys()):
        problems.append(f"表 {t} 没有登记归属模块：在 devtools/check_table_owners.py 的 TABLE_OWNER 里登记")
    for t in sorted(owner_map.keys() - tables):
        problems.append(f"TABLE_OWNER 里的表 {t} 在建表脚本中不存在：删掉这一项")
    for t, owner in sorted(owner_map.items()):
        prefix = MODULE_PREFIX.get(owner)
        if prefix and not t.startswith(prefix):
            problems.append(f"表 {t} 归属模块 {owner}，须以 {prefix} 开头（规则 R4）")
    used: set[tuple[str, str]] = set()
    for rel, ts in found.items():
        mod = module_of(rel)
        if mod is None:
            problems.append(f"{rel} 写了表（{'、'.join(sorted(ts))}），但归不到任何模块：在 FILE_MODULE 里登记")
            continue
        for t in sorted(ts):
            owner = owner_map.get(t)
            if owner is None or owner == mod:
                continue
            if (rel, t) in EXCEPTIONS:
                used.add((rel, t))
                continue
            problems.append(
                f"{rel}（模块 {mod}）写了模块 {owner} 的表 {t}：跨模块只能调对方的 api.py 或发事件（规则 R3）"
            )
    for rel, t in sorted(EXCEPTIONS.keys() - used):
        problems.append(f"例外 ({rel}, {t}) 已不再出现：从 EXCEPTIONS 里删掉（例外只许减、不许增）")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print(f"表归属检查：{len(problems)} 个问题", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"表归属检查通过：{len(TABLE_OWNER)} 张表，登记例外 {len(EXCEPTIONS)} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
