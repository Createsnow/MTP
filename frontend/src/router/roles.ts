/** 菜单与路由用的角色组：系统管理员（唯一）、管理员、普通操作员、审计员。管理员 = 普通操作员 + 操作日志 + 账户管理（限授权组织）。 */
export const ADMIN = ["admin"];
export const OPS = ["admin", "manager", "operator"];
export const AUDIT = ["admin", "manager", "auditor"];
export const ALL = ["admin", "manager", "operator", "auditor"];
/** 账户管理：系统管理员账户 + 管理员（userManage 再按 store.canManageUsers 校验）。 */
export const USER_MANAGE = ["admin", "manager"];
