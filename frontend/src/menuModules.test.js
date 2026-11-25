import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { assembleMenu, moduleEnabled } from "./menuModules.js";

/** 模块化之前 router/index.ts 里 MENU 的菜单组与页面顺序（侧栏、首页快捷入口、权限矩阵都按它）。 */
const BEFORE = [
  ["nav.groupWork", ["/"]],
  ["nav.groupOrder", ["/orders"]],
  ["nav.groupSn", ["/generate", "/sn-import", "/allocate", "/transfer", "/query", "/rules"]],
  ["nav.groupBox", ["/box/generate", "/box/transfer", "/box/query", "/box/rules"]],
  ["nav.groupPallet", ["/pallet/generate", "/pallet/transfer", "/pallet/query"]],
  ["nav.groupLog", ["/audit"]],
  ["nav.groupAccount", ["/users", "/user-groups"]],
  ["nav.groupOrg", ["/factories", "/departments"]],
];

/** 不经 vite 读出各 menu.ts 的 module、order、group 名与路径（只做顺序核对，不执行 TS）。 */
function readMenus() {
  const dir = new URL("./modules/", import.meta.url);
  return readdirSync(dir).map((name) => {
    const src = readFileSync(new URL(`${name}/menu.ts`, dir), "utf8");
    return {
      module: src.match(/module:\s*"([^"]+)"/)[1],
      order: Number(src.match(/order:\s*(\d+)/)[1]),
      group: { group: src.match(/group:\s*"([^"]+)"/)[1], items: [...src.matchAll(/path:\s*"([^"]+)"/g)].map((x) => ({ path: x[1] })) },
    };
  });
}

test("menus collected from modules keep the previous order", () => {
  const menu = assembleMenu(readMenus());
  assert.deepEqual(menu.map((g) => [g.group, g.items.map((i) => i.path)]), BEFORE);
});

test("each item carries its backend module; account and org menus both belong to org", () => {
  const menu = assembleMenu(readMenus());
  const of = (p) => menu.flatMap((g) => g.items).find((i) => i.path === p).module;
  assert.deepEqual(["/", "/orders", "/generate", "/audit", "/users", "/factories"].map(of), ["dashboard", "order", "sn", "log", "org", "org"]);
});

test("duplicate order or path is rejected", () => {
  const g = (name, ...paths) => ({ group: name, items: paths.map((path) => ({ path })) });
  assert.throws(() => assembleMenu([{ module: "a", order: 1, group: g("x", "/a") }, { module: "b", order: 1, group: g("y", "/b") }]), /order 重复/);
  assert.throws(() => assembleMenu([{ module: "a", order: 1, group: g("x", "/a") }, { module: "b", order: 2, group: g("y", "/a") }]), /路径重复/);
});

test("moduleEnabled: unknown list shows all, placeholders always shown, disabled module hidden", () => {
  assert.equal(moduleEnabled({ module: "box" }, null), true);
  assert.equal(moduleEnabled({ module: "box", soon: true }, ["sn"]), true);
  assert.equal(moduleEnabled({ module: "box" }, ["sn"]), false);
  assert.equal(moduleEnabled({ module: "sn" }, ["sn"]), true);
});
