import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";

import { assembleMenu, moduleEnabled } from "@/menuModules.js";
import type { MenuGroup, MenuItem, ModuleMenu } from "./types";

export type { MenuGroup, MenuItem } from "./types";

/** 菜单项对当前账户可见：所属模块已启用（GET /api/modules），角色在内，且满足 superAdmin（只系统管理员账户）/ userManage（能管理账户）。 */
export function menuAllowed(
  m: { roles?: string[]; superAdmin?: boolean; userManage?: boolean; module?: string; soon?: boolean },
  store: { role: string; isSuperAdmin: boolean; canManageUsers: boolean; modules?: string[] | null },
) {
  return (
    moduleEnabled(m, store.modules) &&
    !!m.roles?.includes(store.role) &&
    (!m.superAdmin || store.isSuperAdmin) &&
    (!m.userManage || store.canManageUsers)
  );
}

/**
 * 菜单按业务模块分组（工单、SN、箱号、托号、日志、账户、组织）：每个模块在 src/modules/<模块>/menu.ts 声明一组，
 * 这里自动收集、按 order 排序（见 menuModules.js）。新模块只新建 menu.ts，不改本文件；已有页面的路径不变。
 */
const files = import.meta.glob<{ default: ModuleMenu }>("../modules/*/menu.ts", { eager: true });
export const MENU = assembleMenu(Object.values(files).map((f) => f.default)) as MenuGroup[];

const placeholder = () => import("@/views/Placeholder.vue");

const children = MENU.flatMap((g) => g.items.map((m: MenuItem) => ({ ...m, group: g.group }))).map((m) => ({
  path: m.path === "/" ? "" : m.path.slice(1),
  component: m.view ?? placeholder,
  meta: { roles: m.roles, title: m.title, group: m.group, superAdmin: !!m.superAdmin, userManage: !!m.userManage, soon: !!m.soon, module: m.module },
}));

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: () => import("@/views/Login.vue"), meta: { public: true } },
    { path: "/password", name: "password", component: () => import("@/views/ChangePassword.vue") },
    { path: "/", component: () => import("@/layouts/AppLayout.vue"), children },
    // SN 导出已并入 SN 分配（「已分配单据」页签）；旧链接、书签照常可用。审计员没有分配页，在 SN 查询导出
    { path: "/export", redirect: (to) => ({ path: useAuthStore().canAct ? "/allocate" : "/query", query: to.query.pi ? { pi: to.query.pi } : {} }) },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach(async (to) => {
  const store = useAuthStore();
  if (to.meta.public) return true;
  if (!store.token) return { name: "login", query: { redirect: to.fullPath } };
  await store.ensureModules();
  const roles = to.meta.roles as string[] | undefined;
  if (roles && !roles.includes(store.role)) return { path: "/" };
  if (to.meta.superAdmin && !store.isSuperAdmin) return { path: "/" };
  if (to.meta.userManage && !store.canManageUsers) return { path: "/" };
  if (!moduleEnabled(to.meta as { module?: string; soon?: boolean }, store.modules)) return { path: "/" };
  return true;
});

export default router;
