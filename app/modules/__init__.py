"""业务模块（docs/模块化单体改造方案.md 第 4、8 章）：每个子包一个模块。

每个模块：module.py（装配声明 MODULE）、services/（业务逻辑与 SQL）、routes/（页面接口）、可选 api.py（对外接口）。
模块之间的依赖方向与写表范围由 import-linter（pyproject.toml）与 devtools/check_table_owners.py 检查。
新模块（箱号 box、托号 pallet 已有模板）按方案第 14 章「新模块开发指南」开发，表用 box_ / plt_ 前缀。
"""
