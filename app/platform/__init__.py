"""底座（platform）：所有业务模块共用、不含业务规则的能力（docs/模块化单体改造方案.md 第 4.1 节）。

模块装配（module.py）、应用内事件（events.py）、操作痕迹（audit）、命名锁（locks）、文件下载（files）、
分区与每日维护（partitions，执行各模块登记的每日任务）、按天计数（metrics）、金蝶与 MES 客户端（k3、mes_client）、
演示数据（demo_seed）。配置、数据库、日志、错误码仍在 app/core、app/db、app/middleware。
底座不能引用业务模块（import-linter 契约 platform）。
"""
