/** 首页看板（后端模块 dashboard）。 */
import { ALL } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "dashboard",
  order: 10,
  group: { group: "nav.groupWork", items: [
    { path: "/", title: "nav.dashboard", icon: "Odometer", roles: ALL, view: () => import("@/views/Dashboard.vue") },
  ] },
} satisfies ModuleMenu;
