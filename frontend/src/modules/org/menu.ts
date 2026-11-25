/** 组织管理（后端模块 org）。 */
import { ADMIN, OPS } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "org",
  order: 80,
  group: { group: "nav.groupOrg", icon: "OfficeBuilding", items: [
    { path: "/factories", title: "nav.factories", icon: "OfficeBuilding", roles: ADMIN, view: () => import("@/views/Factories.vue") },
    { path: "/departments", title: "nav.departments", icon: "School", roles: OPS, view: () => import("@/views/Departments.vue") },
  ] },
} satisfies ModuleMenu;
