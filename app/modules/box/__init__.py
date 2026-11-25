"""箱号管理模块（模板，需求未定）：默认不启用，测试环境用 SN_MODULES 打开。

目录约定（docs/模块化单体改造方案.md 第 14 章「新模块开发指南」）：
- module.py   装配声明：路由、启动步骤、后台任务、依赖；default_on=False，需求验收后再改为默认启用
- api.py      对外接口：托号等其他模块只能调这里
- routes.py   页面接口（/api/box/...）
- services.py 业务逻辑；读 SN 只能调 app.modules.sn.api，不能引用 SN 的 services、不能写 SN 的表
- schemas.py  请求体
- 表：用 box_ 前缀，需求定了再加进 V1__init.sql 的「模块 box」一节（加表会改变建表脚本校验和，按运维手册 8.7 处理）
- 错误码：用 BOX_ 前缀，加在 app/core/errors.py，并在 frontend/scripts/error_messages.py 补三语译文

现在只有一个示例接口（POST /api/box/template/sn-check），演示「路由 → 服务 → sn.api」的写法，接入真实功能后删除。
"""
