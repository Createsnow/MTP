/**
 * 菜单按模块收集（docs/模块化单体改造方案.md 第 8.3 节）：每个前端模块在 src/modules/<模块>/menu.ts 声明一组菜单，
 * router/index.ts 用 import.meta.glob 收集后交给 assembleMenu 排序、检查；新增模块不改 router/index.ts。
 *
 * menu.ts 默认导出 { module, order, group }：module 为后端模块名（GET /api/modules 返回的名字，账户与组织两组菜单
 * 都属于后端的 org 模块），order 决定侧栏位置，group 为 { group, icon?, items }。
 */

/**
 * 按 order 排序、检查重复，返回侧栏菜单组；每个菜单项带上所属后端模块（item.module）。
 * @param {Array<{ module: string, order: number, group: { group: string, icon?: string, items: Array<Record<string, any>> } }>} menus
 */
export function assembleMenu(menus) {
  const sorted = [...menus].sort((a, b) => a.order - b.order);
  const orders = new Set();
  const paths = new Set();
  for (const m of sorted) {
    if (orders.has(m.order)) throw new Error(`菜单 order 重复：${m.order}（${m.group.group}）`);
    orders.add(m.order);
    for (const it of m.group.items) {
      if (paths.has(it.path)) throw new Error(`菜单路径重复：${it.path}`);
      paths.add(it.path);
    }
  }
  return sorted.map((m) => ({ ...m.group, module: m.module, items: m.group.items.map((it) => ({ ...it, module: m.module })) }));
}

/**
 * 菜单项所属模块是否启用：还没取到模块清单（null）时都显示；建设中的占位页（soon）一直显示。
 * @param {{ module?: string, soon?: boolean }} item
 * @param {string[] | null | undefined} modules
 */
export function moduleEnabled(item, modules) {
  return !modules || !item.module || !!item.soon || modules.includes(item.module);
}
