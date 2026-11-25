"""底座（platform）：所有业务模块共用、不含业务规则的能力（docs/模块化单体改造方案.md 第 4.1 节）。

现在只有模块装配（module.py）与应用内事件（events.py）；配置、数据库、日志等仍在 app/core、app/db，阶段 4 再搬进来。
底座不能引用业务模块（import-linter 契约 platform）。
"""
