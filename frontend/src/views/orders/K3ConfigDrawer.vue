<template>
  <!-- 生产订单页「金蝶同步设置」：只有系统管理员。两个页签：
       连接参数（金蝶连接：保存时后端先用新参数连接测试，通过才保存；应用密钥不回显）、
       自动同步（定时比对同步间隔、暂停自动同步）。两个页签各自保存，接口与原「金蝶连接」「同步设置」相同 -->
  <el-drawer :model-value="modelValue" size="600px" class="k3-drawer" @update:model-value="emit('update:modelValue', $event)" @open="onOpen">
    <template #header>
      <div class="head">
        <span class="title">{{ t("k3.settingsTitle") }}</span>
        <el-tooltip :content="pane === 'sync' ? t('orders.syncCfg.desc') : t('k3.desc')" placement="bottom" :show-after="200" popper-class="k3-tip">
          <el-icon class="tip"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
    </template>

    <el-tabs v-model="pane" class="panes">
      <el-tab-pane name="conn">
        <template #label>
          <el-badge is-dot :hidden="!connWarn" class="pane-dot">{{ t("k3.paneConn") }}</el-badge>
        </template>
      </el-tab-pane>
      <el-tab-pane name="sync">
        <template #label>
          <el-badge is-dot :hidden="!paused" class="pane-dot">{{ t("k3.paneSync") }}</el-badge>
        </template>
      </el-tab-pane>
    </el-tabs>

    <SyncConfigPanel v-show="pane === 'sync'" ref="syncPanel" @saved="emit('syncSaved', $event)" />

    <div v-show="pane === 'conn'" v-loading="loading" class="body">
      <!-- 状态栏：来源 · 最近测试 · 最后修改 -->
      <div v-if="cfg" class="status">
        <div class="cell">
          <div class="k">{{ t("k3.source") }}</div>
          <el-tooltip :content="t(`k3.sourceHint${cfg.source}`)" placement="bottom">
            <el-tag size="small" :type="cfg.source === 'PAGE' ? 'primary' : 'info'">{{ t(`k3.source${cfg.source}`) }}</el-tag>
          </el-tooltip>
        </div>
        <div class="cell">
          <div class="k">{{ t("k3.lastTest") }}</div>
          <div v-if="cfg.last_test" class="v">
            <el-tag size="small" :type="cfg.last_test.ok ? 'success' : 'danger'">{{ cfg.last_test.ok ? t("k3.testPassed") : t("k3.testFailedTag") }}</el-tag>
            <span class="muted">{{ fmt(cfg.last_test.at, false) }}</span>
          </div>
          <div v-else class="v muted">{{ t("k3.never") }}</div>
        </div>
        <div class="cell">
          <div class="k">{{ t("k3.updated") }}</div>
          <div class="v">{{ cfg.updated_at ? `${fmt(cfg.updated_at, false)} · ${cfg.updated_by}` : "—" }}</div>
        </div>
      </div>

      <el-alert v-if="cfg?.secret_unreadable" type="error" :closable="false" show-icon class="mb" :title="t('k3.secretUnreadable')" />
      <el-alert v-if="result" :type="result.ok ? 'success' : 'error'" :closable="false" show-icon class="mb"
        :title="result.ok ? t('k3.testOk', { orgs: result.orgs, sec: (result.elapsed_ms / 1000).toFixed(1) }) : t('k3.testFail', { message: result.message })" />

      <el-form label-position="top" class="grid" @submit.prevent>
        <el-form-item class="span2" required>
          <template #label><Lbl :text="t('k3.serverUrl')" :tip="t('k3.serverUrlHint')" /></template>
          <el-input v-model.trim="form.server_url" class="sn-mono" placeholder="http://10.0.0.1/k3cloud/" />
        </el-form-item>
        <el-form-item required>
          <template #label><Lbl :text="t('k3.acctId')" :tip="t('k3.acctIdHint')" /></template>
          <el-input v-model.trim="form.acct_id" class="sn-mono" />
        </el-form-item>
        <el-form-item required>
          <template #label><Lbl :text="t('k3.username')" :tip="t('k3.usernameHint')" /></template>
          <el-input v-model.trim="form.username" />
        </el-form-item>
        <el-form-item class="span2" :label="t('k3.appId')" required>
          <el-input v-model.trim="form.app_id" class="sn-mono" />
        </el-form-item>
        <el-form-item :required="!cfg?.secret_set || cfg?.secret_unreadable">
          <template #label><Lbl :text="t('k3.appSecret')" :tip="t('k3.secretHint')" /></template>
          <el-input v-model="form.app_secret" type="password" show-password autocomplete="new-password"
            :placeholder="cfg?.secret_set && !cfg?.secret_unreadable ? t('k3.secretKeep') : ''" />
        </el-form-item>
        <el-form-item :label="t('k3.lcid')">
          <el-select v-model="form.lcid" class="full">
            <el-option v-for="l in LCIDS" :key="l" :value="l" :label="t(`k3.lcid${l}`)" />
          </el-select>
        </el-form-item>
      </el-form>

      <el-collapse v-model="open" class="fold">
        <el-collapse-item name="adv">
          <template #title>
            <span class="fold-title">{{ t("k3.advanced") }}<span class="muted">{{ t("k3.advancedHint") }}</span></span>
          </template>
          <el-form label-position="top" class="grid grid3" @submit.prevent>
            <el-form-item :label="t('k3.loginTimeoutSec')"><el-input-number v-model="form.login_timeout" :min="5" :max="300" controls-position="right" class="full" /></el-form-item>
            <el-form-item :label="t('k3.queryTimeoutSec')"><el-input-number v-model="form.query_timeout" :min="10" :max="600" controls-position="right" class="full" /></el-form-item>
            <el-form-item :label="t('k3.viewTimeoutSec')"><el-input-number v-model="form.view_timeout" :min="5" :max="300" controls-position="right" class="full" /></el-form-item>
            <el-form-item :label="t('k3.retryTimes')"><el-input-number v-model="form.page_retries" :min="0" :max="10" controls-position="right" class="full" /></el-form-item>
            <el-form-item :label="t('k3.retryInterval')"><el-input-number v-model="form.page_retry_seconds" :min="0" :max="120" controls-position="right" class="full" /></el-form-item>
          </el-form>
        </el-collapse-item>
        <el-collapse-item name="history">
          <template #title>
            <span class="fold-title">{{ t("k3.history") }}<span v-if="cfg?.history?.length" class="muted">（{{ cfg.history.length }}）</span><HelpTip :content="t('k3.historyHint')" /></span>
          </template>
          <ul v-if="cfg?.history?.length" class="history">
            <li v-for="(item, i) in cfg.history" :key="i">
              <span class="when">{{ fmt(item.at, false) }} · {{ item.by }}</span>
              <span>{{ historyText(item) }}</span>
            </li>
          </ul>
          <div v-else class="muted">{{ t("k3.historyEmpty") }}</div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <template #footer>
      <div v-if="pane === 'sync'" class="foot">
        <el-button @click="emit('update:modelValue', false)">{{ t("common.cancel") }}</el-button>
        <el-button type="primary" :loading="!!syncPanel?.saving" :disabled="!syncPanel?.ready" @click="saveSync">{{ t("common.save") }}</el-button>
      </div>
      <div v-else class="foot">
        <el-tooltip :content="cfg?.source === 'PAGE' ? t('k3.resetTip') : t('k3.resetDisabled')" placement="top">
          <span class="reset">
            <el-button text :loading="busy === 'reset'" :disabled="!!busy || cfg?.source !== 'PAGE'" @click="reset">
              <el-icon><RefreshLeft /></el-icon><span>{{ t("k3.reset") }}</span>
            </el-button>
          </span>
        </el-tooltip>
        <el-button @click="emit('update:modelValue', false)">{{ t("common.cancel") }}</el-button>
        <el-button :loading="busy === 'test'" :disabled="!!busy" @click="test"><el-icon><Connection /></el-icon><span>{{ t("k3.test") }}</span></el-button>
        <el-button type="primary" :loading="busy === 'save'" :disabled="!!busy" @click="save">{{ t("common.save") }}</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ElIcon, ElMessage, ElMessageBox, ElTooltip } from "element-plus";
import { QuestionFilled } from "@element-plus/icons-vue";
import { api } from "@/api";
import { useTime } from "@/composables/useTime";
import SyncConfigPanel from "./SyncConfigPanel.vue";
import HelpTip from "@/components/HelpTip.vue";

/** tab：打开时显示哪个页签（conn 连接参数 / sync 自动同步）；paused：自动同步已暂停（页签上的红点） */
const props = defineProps<{ modelValue: boolean; tab?: "conn" | "sync"; paused?: boolean }>();
const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "saved", cfg: any): void;
  (e: "syncSaved", cfg: any): void;
}>();

const { t } = useI18n();
const { fmt } = useTime();

const LCIDS = [2052, 1033, 3076];
const cfg = ref<any>(null);
const loading = ref(false);
const busy = ref<"" | "test" | "save" | "reset">("");
const result = ref<any>(null);
const open = ref<string[]>([]);
const pane = ref<"conn" | "sync">("conn");
const syncPanel = ref<InstanceType<typeof SyncConfigPanel> | null>(null);
/** 连接参数页签红点：最近测试未通过或密钥解不开（与页面按钮上的红点同一条件） */
const connWarn = computed(() => !!cfg.value && (cfg.value.secret_unreadable || cfg.value.last_test?.ok === false));

/** 打开抽屉：切到指定页签，两个页签的设置都重新读一次 */
function onOpen() {
  pane.value = props.tab || "conn";
  load();
  syncPanel.value?.load();
}

async function saveSync() {
  if (await syncPanel.value?.save()) emit("update:modelValue", false);
}

/** 表单标签：文字 + 问号图标（悬停显示说明），代替输入框下方的说明文字 */
const Lbl = defineComponent({
  props: { text: { type: String, required: true }, tip: { type: String, default: "" } },
  setup(props) {
    return () =>
      h("span", { class: "lbl" }, [
        props.text,
        props.tip
          ? h(ElTooltip, { content: props.tip, placement: "top", popperClass: "k3-tip" }, () =>
              h(ElIcon, { class: "lbl-tip" }, () => h(QuestionFilled)),
            )
          : null,
      ]);
  },
});
const form = reactive({
  server_url: "", acct_id: "", app_id: "", app_secret: "", username: "", lcid: 2052,
  login_timeout: 30, query_timeout: 120, view_timeout: 60, page_retries: 3, page_retry_seconds: 10,
});

function fill(c: any) {
  cfg.value = c;
  Object.assign(form, {
    server_url: c.server_url || "", acct_id: c.acct_id || "", app_id: c.app_id || "", app_secret: "", username: c.username || "",
    lcid: LCIDS.includes(c.lcid) ? c.lcid : 2052, login_timeout: c.login_timeout, query_timeout: c.query_timeout,
    view_timeout: c.view_timeout, page_retries: c.page_retries, page_retry_seconds: c.page_retry_seconds,
  });
}

async function load() {
  result.value = null;
  loading.value = true;
  try {
    fill(await api.k3Config());
  } finally {
    loading.value = false;
  }
}

/** 应用密钥留空 = 沿用当前的（不提交）。 */
const payload = () => ({ ...form, app_secret: form.app_secret.trim() || undefined });

const historyText = (x: any) => {
  if (x.reset) return t("k3.historyReset");
  if (x.from_env) return t("k3.historyFromEnv");
  if (!x.fields?.length) return t("k3.historyNoChange");
  return t("k3.historyChanged", { fields: x.fields.map((f: string) => t(`k3.field.${f}`)).join(t("k3.sep")) });
};

async function test() {
  busy.value = "test";
  try {
    result.value = await api.testK3Config(payload());
    cfg.value = { ...cfg.value, ...(await api.k3Config().catch(() => ({}))) };
  } finally {
    busy.value = "";
  }
}

/** 恢复默认：确认将改用的 .env 参数（不含密钥），后端先用它连接测试，通过才删掉页面保存的参数。 */
async function reset() {
  const env = cfg.value?.env || {};
  const rows: [string, string][] = [
    [t("k3.serverUrl"), env.server_url || "—"],
    [t("k3.acctId"), env.acct_id || "—"],
    [t("k3.appId"), env.app_id || "—"],
    [t("k3.username"), env.username || "—"],
    [t("k3.appSecret"), env.secret_set ? t("k3.envSecretSet") : t("k3.envSecretMissing")],
  ];
  const message = h("div", [
    h("p", { style: "margin:0 0 8px" }, t("k3.resetConfirm")),
    h("div", { style: "display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:13px" },
      rows.flatMap(([k, v]) => [h("span", { style: "color:var(--app-muted)" }, k), h("span", { style: "word-break:break-all" }, v)])),
  ]);
  try {
    await ElMessageBox.confirm(message, t("k3.reset"), { type: "warning", confirmButtonText: t("k3.reset"), cancelButtonText: t("common.cancel") });
  } catch {
    return;
  }
  busy.value = "reset";
  try {
    const c = await api.resetK3Config();
    fill(c);
    result.value = null;
    ElMessage.success(t("k3.resetDone"));
    emit("saved", c);
  } catch {
    // .env 参数连接测试未通过：错误已由请求拦截器提示；刷新「最近测试」
    cfg.value = { ...cfg.value, ...(await api.k3Config().catch(() => ({}))) };
  } finally {
    busy.value = "";
  }
}

async function save() {
  busy.value = "save";
  try {
    const c = await api.saveK3Config(payload());
    fill(c);
    result.value = null;
    ElMessage.success(t("k3.saved"));
    emit("saved", c);
  } catch {
    // 连接测试未通过等错误已由请求拦截器提示；刷新「最近测试」
    cfg.value = { ...cfg.value, ...(await api.k3Config().catch(() => ({}))) };
  } finally {
    busy.value = "";
  }
}
</script>

<style scoped>
.head { display: flex; align-items: center; gap: 6px; }
.title { font-size: 16px; font-weight: 600; color: var(--app-text); }
.tip { color: var(--app-muted); cursor: help; }
.body { padding: 0 2px; }
.panes { margin-top: -8px; }
.panes :deep(.el-tabs__header) { margin-bottom: 16px; }
.pane-dot :deep(.el-badge__content.is-dot) { top: 8px; right: -2px; }
.status { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 18px;
  border: 1px solid var(--app-line); border-radius: 8px; background: #f8fafc; }
.cell { padding: 10px 14px; min-width: 0; }
.cell + .cell { border-left: 1px solid var(--app-line); }
.cell .k { font-size: 12px; color: var(--app-muted); margin-bottom: 6px; }
.cell .v { display: flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 16px; }
.grid3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.grid .span2 { grid-column: span 2; }
.grid :deep(.el-form-item) { margin-bottom: 16px; }
.grid :deep(.el-form-item__label) { line-height: 20px; margin-bottom: 6px; padding: 0; }
.full { width: 100%; }
.mb { margin-bottom: 14px; }
:deep(.lbl) { display: inline-flex; align-items: center; gap: 4px; }
:deep(.lbl-tip) { color: #94a3b8; cursor: help; font-size: 13px; }
.fold { border-top: 1px solid var(--app-line); border-bottom: none; margin-top: 4px; }
.fold :deep(.el-collapse-item__header) { height: 44px; font-size: 13px; }
.fold :deep(.el-collapse-item__content) { padding-bottom: 8px; }
.fold-title { font-weight: 600; color: #334155; }
.fold-title .muted { font-weight: 400; margin-left: 4px; }
.history { list-style: none; margin: 0; padding: 0; }
.history li { display: flex; gap: 12px; font-size: 13px; padding: 6px 0; border-bottom: 1px dashed var(--app-line); }
.history li:last-child { border-bottom: none; }
.history .when { flex: none; width: 190px; white-space: nowrap; color: var(--app-muted); font-variant-numeric: tabular-nums; }
.foot { display: flex; justify-content: flex-end; align-items: center; }
.reset { margin-right: auto; }
</style>

<style>
/* 抽屉挂在 body 下，scoped 样式作用不到头部 */
.k3-drawer .el-drawer__header { margin-bottom: 0; padding-bottom: 16px; }
.k3-tip { max-width: 320px; line-height: 1.6; }
</style>
