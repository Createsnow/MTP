/** 箱号管理（后端模块 box，建设中：页面为占位页，接入后把 soon 去掉、填 view）。 */
import { ALL, OPS } from "@/router/roles";
import type { ModuleMenu } from "@/router/types";

export default {
  module: "box",
  order: 40,
  group: { group: "nav.groupBox", icon: "Box", items: [
    { path: "/box/generate", title: "nav.boxGenerate", icon: "Finished", roles: OPS, soon: true },
    { path: "/box/transfer", title: "nav.boxTransfer", icon: "Switch", roles: OPS, soon: true },
    { path: "/box/query", title: "nav.boxQuery", icon: "Search", roles: ALL, soon: true },
    { path: "/box/rules", title: "nav.boxRules", icon: "SetUp", roles: OPS, soon: true },
  ] },
} satisfies ModuleMenu;
