<template>
  <!-- 说明性提示语统一收进问号图标：鼠标移到（或键盘聚焦）时显示，不占页面；content 中的换行按分段显示 -->
  <el-tooltip :content="content" :placement="placement" :show-after="150" popper-class="help-tip-pop">
    <el-icon class="help-tip" tabindex="0" :aria-label="content" @click.stop.prevent><QuestionFilled /></el-icon>
  </el-tooltip>
</template>

<script setup lang="ts">
import { QuestionFilled } from "@element-plus/icons-vue";

withDefaults(defineProps<{ content: string; placement?: string }>(), { placement: "top" });
</script>
