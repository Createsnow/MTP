"""页面接口与 MES 接口的路由文件。挂哪些、按什么顺序挂，由各模块的 app/modules/<模块>/module.py 声明，
main.py 通过 app.platform.module.api_router 装配；这里不再手工罗列。health 由 main.py 直接挂在根路径。"""

from app.api.routes import health

__all__ = ["health"]
