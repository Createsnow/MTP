"""托号管理模块（模板，需求未定）：默认不启用，依赖箱号模块，测试环境用 SN_MODULES 打开（须同时打开 box）。

目录约定同箱号模块（见 app/modules/box/__init__.py）；表用 plt_ 前缀，错误码用 PLT_ 前缀。
读箱号只能调 app.modules.box.api，读 SN 只能调 app.modules.sn.api。

现在只有一个示例接口（POST /api/pallet/template/sn-check），接入真实功能后删除。
"""
