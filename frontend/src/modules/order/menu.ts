/** 工单（后端模块 order）。 */
import { OPS } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "order",
  order: 20,
  group: { group: "nav.groupOrder", icon: "Tickets", items: [
    { path: "/orders", title: "nav.orders", icon: "Tickets", roles: OPS, view: () => import("@/views/Orders.vue") },
  ] },
} satisfies ModuleMenu;
