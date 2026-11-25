"""组织模块对外接口：其他模块只能调这里写组织的表（阶段 4c）。"""

from __future__ import annotations

from app.db.session import db


def set_mes_cfg_ok(factory_code: str) -> None:
    """MES 推送应答正常：组织的对接配置状态记为 OK（由 SN 推送代码原样搬来，在调用方的事务内执行）。"""
    db.exec("UPDATE sn_factory SET cfg_status = 'OK', cfg_message = '' WHERE factory_code = %s", factory_code)


def set_mes_cfg_failed(factory_code: str, message: str) -> None:
    """MES 推送被拒绝且是配置问题：组织的对接配置状态记为 FAILED（message 由调用方截断）。"""
    db.exec(
        "UPDATE sn_factory SET cfg_status = 'FAILED', cfg_message = %s WHERE factory_code = %s",
        message,
        factory_code,
    )
