"""补建缺失的表：已建好的库按 V1 里的建表语句只建库里还没有的表，已有的表和数据一律不动。

V1 新增表（如双 SN 的 sn_pi_dual、sn_pi_rule2、sn_prefix_role、sn_item_sub、sn_item_sub_deleted）后，已建好的库
启动时只记 migration_checksum_mismatch 警告、不会重跑 V1，启动检查会报缺表；用本工具补建这几张表即可，
不必清库重建（测试库与 MES 联调的数据保留）。

    uv run python -m devtools.create_missing_tables          # 只检查，列出缺哪些表
    uv run python -m devtools.create_missing_tables --yes    # 按 V1 建缺失的表（空表）

读仓库根 .env 的 DB_*（与启动服务相同）。
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import settings
from app.db.migrate import create_statement, schema_tables
from app.db.session import db


def missing() -> list[str]:
    have = set(db.column("SHOW TABLES"))
    return [t for t in schema_tables() if t not in have]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--yes", action="store_true", help="按 V1 建缺失的表")
    a = p.parse_args(argv)
    db.configure(settings.db_target(), 1)
    t = db.target
    print(f"数据库 {t.host}:{t.port}/{t.database}")
    todo = missing()
    if not todo:
        print("没有缺失的表")
        return 0
    print("缺失的表：" + "、".join(todo))
    if not a.yes:
        print("加 --yes 执行：按 V1 建以上空表（已有的表和数据不动）")
        return 0
    for name in todo:
        db.exec(create_statement(name))
        print(f"已建 {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
