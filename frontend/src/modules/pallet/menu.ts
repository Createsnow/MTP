/** 托号管理（后端模块 pallet，建设中：页面为占位页，接入后把 soon 去掉、填 view）。 */
import { ALL, OPS } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "pallet",
  order: 50,
  group: { group: "nav.groupPallet", icon: "Van", items: [
    { path: "/pallet/generate", title: "nav.palletGenerate", icon: "Finished", roles: OPS, soon: true },
    { path: "/pallet/transfer", title: "nav.palletTransfer", icon: "Switch", roles: OPS, soon: true },
    { path: "/pallet/query", title: "nav.palletQuery", icon: "Search", roles: ALL, soon: true },
  ] },
} satisfies ModuleMenu;
