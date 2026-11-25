/** 账户管理（后端模块 org）。 */
import { ADMIN, USER_MANAGE } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "org",
  order: 70,
  group: { group: "nav.groupAccount", icon: "User", items: [
    { path: "/users", title: "nav.users", icon: "User", roles: USER_MANAGE, userManage: true, view: () => import("@/views/Users.vue") },
    { path: "/user-groups", title: "nav.userGroups", icon: "UserFilled", roles: ADMIN, superAdmin: true, view: () => import("@/views/UserGroups.vue") },
  ] },
} satisfies ModuleMenu;
