<template>
  <!-- 使用规则 / 前缀的 PI：生成过号的（绿）在前，已选未生成的（灰）在后；多了折叠为 +n，悬停看全部 -->
  <span v-if="all.length" class="pi-tags">
    <el-tag v-for="p in head" :key="p.pi" size="small" :type="p.used ? 'success' : 'info'" effect="plain" class="sn-mono"
      :title="p.used ? undefined : t('rules.pisSelected')">{{ p.pi }}</el-tag>
    <el-tooltip v-if="rest.length" placement="top">
      <template #content><div class="pi-more">{{ rest.map((p) => p.pi).join("、") }}</div></template>
      <el-tag size="small" type="info">+{{ rest.length }}</el-tag>
    </el-tooltip>
  </span>
  <span v-else class="muted">—</span>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = withDefaults(defineProps<{ used?: string[]; picked?: string[]; max?: number }>(), { used: () => [], picked: () => [], max: 4 });
const { t } = useI18n();
const all = computed(() => [...props.used.map((pi) => ({ pi, used: true })), ...props.picked.map((pi) => ({ pi, used: false }))]);
const head = computed(() => all.value.slice(0, props.max));
const rest = computed(() => all.value.slice(props.max));
</script>

<style scoped>
.pi-tags { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.pi-more { max-width: 360px; word-break: break-all; }
.muted { color: var(--app-muted); }
</style>
