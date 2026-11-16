<template>
  <!-- 「金蝶同步设置」抽屉的「自动同步」页签：定时比对同步间隔、暂停自动同步（定时、进页）；手动同步不受影响。
       保存按钮在抽屉底部，通过 defineExpose 调用 save() -->
  <div v-loading="loading">
    <el-form label-position="top" class="form" @submit.prevent>
      <el-form-item :label="t('orders.syncCfg.auto')">
        <el-switch v-model="form.running" :active-text="t('orders.syncCfg.running')" :inactive-text="t('orders.syncCfg.paused')" inline-prompt
          style="--el-switch-off-color: var(--el-color-warning)" />
      </el-form-item>
      <el-form-item :label="t('orders.syncCfg.interval')">
        <el-input-number v-model="form.interval" :min="cfg?.min_minutes ?? 5" :max="cfg?.max_minutes ?? 1440" :step="5"
          controls-position="right" class="w160" />
        <span class="unit">{{ t("orders.syncCfg.minutes") }}</span>
        <div class="muted sub">
          {{ cfg?.source === "PAGE" ? t("orders.syncCfg.sourcePage") : t("orders.syncCfg.sourceEnv", { n: cfg?.env_interval_minutes ?? "—" }) }}
        </div>
      </el-form-item>
      <el-form-item :label="t('orders.syncCfg.updated')">
        <span>{{ cfg?.updated_at ? `${fmt(cfg.updated_at, false)} · ${cfg.updated_by}` : "—" }}</span>
      </el-form-item>
    </el-form>
    <el-alert v-if="!form.running" type="warning" :closable="false" show-icon :title="t('orders.syncCfg.pausedHint')" />
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import { api } from "@/api";
import { useTime } from "@/composables/useTime";

const emit = defineEmits<{ saved: [any] }>();
const { t } = useI18n();
const { fmt } = useTime();

const cfg = ref<any>(null);
const form = reactive({ running: true, interval: 10 as number | undefined });
const loading = ref(false);
const saving = ref(false);
/** 抽屉底部「保存」可点：设置已读到且间隔有值 */
const ready = computed(() => !!cfg.value && !!form.interval);

async function load() {
  loading.value = true;
  try {
    cfg.value = await api.syncConfig();
    form.running = !cfg.value.paused;
    form.interval = cfg.value.interval_minutes;
  } finally {
    loading.value = false;
  }
}

/** 保存成功返回 true（抽屉随即关闭）；失败的提示由请求拦截器显示 */
async function save(): Promise<boolean> {
  if (!cfg.value || !form.interval) return false;
  saving.value = true;
  try {
    // 间隔没改且来自 .env 时不提交间隔（继续跟随 .env）
    const keepEnv = cfg.value.source === "ENV" && form.interval === cfg.value.interval_minutes;
    const r = await api.saveSyncConfig({ paused: !form.running, ...(keepEnv ? {} : { interval_minutes: form.interval }) });
    cfg.value = r;
    ElMessage.success(t("orders.syncCfg.saved"));
    emit("saved", r);
    return true;
  } catch {
    return false;
  } finally {
    saving.value = false;
  }
}

defineExpose({ load, save, saving, ready });
</script>

<style scoped>
.form :deep(.el-form-item__label) { line-height: 20px; margin-bottom: 6px; padding: 0; }
.w160 { width: 160px; }
.unit { margin-left: 8px; }
.sub { flex-basis: 100%; font-size: 12px; line-height: 1.6; margin-top: 4px; }
</style>
