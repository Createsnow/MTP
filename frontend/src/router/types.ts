/** 菜单项；soon = 建设中的占位页（菜单标「建设中」，页面用 Placeholder.vue）。view 为页面组件，不填即占位页。 */
export type MenuItem = {
  path: string;
  title: string;
  icon: string;
  roles: string[];
  superAdmin?: boolean;
  userManage?: boolean;
  soon?: boolean;
  view?: () => Promise<unknown>;
  /** 所属后端模块（由 assembleMenu 填） */
  module?: string;
};
export type MenuGroup = { group: string; icon?: string; module?: string; items: MenuItem[] };
/** src/modules/<模块>/menu.ts 的默认导出。module = 后端模块名（GET /api/modules），order = 侧栏位置。 */
export type ModuleMenu = { module: string; order: number; group: MenuGroup };
