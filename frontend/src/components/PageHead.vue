<template>
  <div class="page__head">
    <!-- 页面说明收进标题旁的问号，鼠标移到时显示 -->
    <h2 class="page__title">
      {{ title }}
      <HelpTip v-if="desc" :content="desc" placement="bottom-start" />
    </h2>
    <div class="page__actions"><slot /></div>
  </div>
</template>

<script setup lang="ts">
import HelpTip from "./HelpTip.vue";

defineProps<{ title: string; desc?: string }>();
</script>
