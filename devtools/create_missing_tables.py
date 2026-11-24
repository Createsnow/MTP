"""补建缺失的表与字段：已建好的库按 V1 只建库里还没有的表、给已有的表补上还没有的字段，已有的数据一律不动。

V1 新增表（如双 SN 的 sn_pi_dual、sn_pi_rule2、sn_prefix_role、sn_item_sub、sn_item_sub_deleted）或给已有的表
新增带默认值的字段（如 2026-11-24 附件项次组件的 sn_rule_version.attach_width、attach_tail）后，已建好的库
启动时只记 migration_checksum_mismatch 警告、不会重跑 V1，启动检查会报缺表 / 缺字段；用本工具补建即可，
不必清库重建（测试库与 MES 联调的数据保留）。补字段按 V1 的字段定义 ALTER TABLE … ADD COLUMN … AFTER 前一个字段，
只补可空或带默认值的字段（已有的行取默认值）；NOT NULL 又没有默认值的字段不补，报出来按运维手册重建。

    uv run python -m devtools.create_missing_tables          # 只检查，列出缺哪些表、字段
    uv run python -m devtools.create_missing_tables --yes    # 按 V1 建缺失的表（空表）、补缺失的字段

读仓库根 .env 的 DB_*（与启动服务相同）。
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import settings
from app.db.migrate import create_statement, missing_columns, schema_tables
from app.db.session import db


def missing() -> list[str]:
    have = set(db.column("SHOW TABLES"))
    return [t for t in schema_tables() if t not in have]


def addable(definition: str) -> bool:
    """字段能否原地补上：可空，或带默认值（已有的行取默认值）。"""
    d = definition.upper()
    return "NOT NULL" not in d or " DEFAULT " in d or "AUTO_INCREMENT" in d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--yes", action="store_true", help="按 V1 建缺失的表")
    a = p.parse_args(argv)
    db.configure(settings.db_target(), 1)
    t = db.target
    print(f"数据库 {t.host}:{t.port}/{t.database}")
    todo = missing()
    cols = missing_columns(db)
    if not todo and not cols:
        print("没有缺失的表、字段")
        return 0
    if todo:
        print("缺失的表：" + "、".join(todo))
    if cols:
        print("缺失的字段：" + "、".join(f"{t}.{c}" for t, c, _d, _p in cols))
    bad = [f"{t}.{c}" for t, c, d, _p in cols if not addable(d)]
    if bad:
        print("以下字段 NOT NULL 又没有默认值，不能原地补，什么也没改；请按运维手册 8.7 重建：" + "、".join(bad))
        return 1
    if not a.yes:
        print("加 --yes 执行：按 V1 建以上空表、补以上字段（已有的表和数据不动）")
        return 0
    for name in todo:
        db.exec(create_statement(name))
        print(f"已建 {name}")
    for t, c, d, prev in cols:
        db.exec(f"ALTER TABLE {t} ADD COLUMN {d}" + (f" AFTER {prev}" if prev else " FIRST"))
        print(f"已补 {t}.{c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
