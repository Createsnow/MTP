/** SN 管理（后端模块 sn）。 */
import { ALL, OPS } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "sn",
  order: 30,
  group: { group: "nav.groupSn", icon: "Stamp", items: [
    { path: "/generate", title: "nav.generate", icon: "Finished", roles: OPS, view: () => import("@/views/Generate.vue") },
    { path: "/sn-import", title: "nav.snImport", icon: "Upload", roles: OPS, view: () => import("@/views/SnImport.vue") },
    { path: "/allocate", title: "nav.allocate", icon: "Promotion", roles: OPS, view: () => import("@/views/Allocate.vue") },
    { path: "/transfer", title: "nav.transfer", icon: "Switch", roles: OPS, view: () => import("@/views/Transfer.vue") },
    { path: "/query", title: "nav.query", icon: "Search", roles: ALL, view: () => import("@/views/Query.vue") },
    { path: "/rules", title: "nav.rules", icon: "SetUp", roles: OPS, view: () => import("@/views/Rules.vue") },
  ] },
} satisfies ModuleMenu;
