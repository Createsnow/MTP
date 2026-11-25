/** 日志（后端模块 log）。 */
import { AUDIT } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "log",
  order: 60,
  group: { group: "nav.groupLog", icon: "Notebook", items: [
    { path: "/audit", title: "nav.audit", icon: "Notebook", roles: AUDIT, view: () => import("@/views/Audit.vue") },
  ] },
} satisfies ModuleMenu;
