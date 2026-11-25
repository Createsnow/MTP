"""组织 / 账户模块的装配声明。"""

from __future__ import annotations

import structlog

from app.core.config import settings
from app.modules.org.routes import auth, factories, users
from app.modules.org.services import daily as org_daily
from app.modules.org.services import users as users_svc
from app.platform.module import Module, Step

log = structlog.get_logger("app.main")


def init_admin() -> None:
    """按 .env 建系统管理员（空库）或重置它（SN_RESET_ADMIN=true）。不强制改密，所以生产环境不允许默认密码。"""
    reset = settings.get_bool("SN_RESET_ADMIN", False)
    if not reset and not users_svc.needs_bootstrap():
        return
    problem = settings.admin_password_problem()
    if problem is not None:
        if settings.production:
            raise RuntimeError(f"{problem}：生产环境必须在 .env 里设置 SN_INIT_ADMIN_PASSWORD")
        log.warning("weak_admin_password", problem=problem, note="ENVIRONMENT=production 时将拒绝启动")
    emp = settings.init_admin_emp_no
    if reset:
        users_svc.reset_admin(settings.init_admin_password)
        log.warning("admin_reset", emp_no=emp, note="password=SN_INIT_ADMIN_PASSWORD; remove SN_RESET_ADMIN now")
    elif users_svc.bootstrap_admin(settings.init_admin_password):
        log.warning("bootstrap_admin", emp_no=emp, note="password=SN_INIT_ADMIN_PASSWORD")


MODULE = Module(
    name="org",
    title="组织 / 账户",
    order=10,
    required=True,
    routers=(auth.router, users.router, factories.router),
    startup=(Step("init_admin", 40, init_admin),),
    daily=(Step("factory_sync", 20, org_daily.sync_factories),),
)
