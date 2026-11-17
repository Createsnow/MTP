<template>
  <div class="page">
    <PageHead :title="t('rules.title')" :desc="t('rules.desc')">
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon><span>{{ t("rules.create") }}</span></el-button>
    </PageHead>
    <div class="surface">
      <div class="toolbar">
        <el-select v-model="piFilter" filterable clearable :placeholder="t('rules.filterPi')">
          <el-option v-for="v in piValues" :key="v" :value="v" :label="v" />
        </el-select>
        <el-input v-model="q" clearable :placeholder="t('rules.search')" prefix-icon="Search" @keyup.enter="search" @clear="search" />
        <el-button @click="search">{{ t("common.search") }}</el-button>
      </div>
      <!-- 按前缀归档：一个前缀一行（进制、流水位数锁定），展开为该前缀下的规则及使用它们的 PI；点「详情」看各 PI 占的流水与示例号 -->
      <el-table v-loading="loading" :data="paged" size="small" row-key="prefix" :empty-text="t('common.empty')">
        <el-table-column type="expand" width="36">
          <template #default="{ row: g }">
            <el-table :data="g.rules" size="small" class="inner" :row-key="(x: any) => x.rule.id">
              <el-table-column :label="t('rules.code')" min-width="140" show-overflow-tooltip>
                <template #default="{ row }"><span class="sn-mono">{{ row.rule.rule_code }}</span></template>
              </el-table-column>
              <el-table-column :label="t('rules.name')" min-width="140" show-overflow-tooltip><template #default="{ row }">{{ row.rule.rule_name }}</template></el-table-column>
              <el-table-column :label="t('rules.format')" min-width="240">
                <template #default="{ row }">
                  <span class="muted small">{{ row.version?.format }}</span>
                  <el-tag v-if="!row.is_current_prefix && row.current" size="small" type="warning" effect="plain" class="ml">
                    {{ t("rules.otherPrefix", { prefix: row.current.prefix }) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="t('rules.version')" width="64"><template #default="{ row }">v{{ row.rule.current_version }}</template></el-table-column>
              <el-table-column :label="t('rules.pis')" min-width="200"><template #default="{ row }"><PiTags :used="row.pis" :picked="row.selected_pis" /></template></el-table-column>
              <el-table-column :label="t('rules.factory')" min-width="120" show-overflow-tooltip>
                <template #default="{ row }">
                  <el-tag v-if="!row.rule.factory_code" size="small" type="info" effect="plain">{{ t("rules.ownerGlobal") }}</el-tag>
                  <span v-else>{{ factoryName(row.rule.factory_code) }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="t('common.actions')" width="110" fixed="right">
                <template #default="{ row }">
                  <el-tooltip :disabled="editable(row)" :content="t('rules.globalReadonly')">
                    <span><el-button link type="primary" :disabled="!editable(row)" @click="openEdit(row)">{{ t("common.edit") }}</el-button></span>
                  </el-tooltip>
                  <el-tooltip :disabled="editable(row) && deletable(row)" :content="editable(row) ? t('rules.deleteBlocked') : t('rules.globalReadonly')">
                    <span class="ml"><el-button link type="danger" :disabled="!editable(row) || !deletable(row)" @click="remove(row)">{{ t("common.delete") }}</el-button></span>
                  </el-tooltip>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column :label="t('rules.colPrefix')" min-width="120">
          <template #default="{ row }"><b class="sn-mono">{{ row.prefix }}</b></template>
        </el-table-column>
        <el-table-column :label="t('rules.seqFormat')" min-width="120">
          <template #default="{ row }">{{ t("rules.seqFormatVal", { base: row.base, n: row.seq_len }) }}</template>
        </el-table-column>
        <el-table-column :label="t('rules.ruleCount')" width="72"><template #default="{ row }">{{ t("rules.ruleCountVal", { n: row.rules.length }) }}</template></el-table-column>
        <el-table-column :label="t('rules.pis')" min-width="220"><template #default="{ row }"><PiTags :used="row.pis" :picked="row.selected_pis" /></template></el-table-column>
        <el-table-column :label="t('rules.topSeq')" width="110" align="right">
          <template #default="{ row }"><span v-if="row.top_seq" class="sn-mono">{{ row.top_text }}</span><span v-else class="muted">—</span></template>
        </el-table-column>
        <el-table-column :label="t('rules.factory')" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.factories.map((f: string | null) => (f ? factoryName(f) : t("rules.ownerGlobal"))).join("、") }}</template>
        </el-table-column>
        <el-table-column :label="t('rules.status')" width="90">
          <template #default="{ row }"><el-tag size="small" :type="row.used ? 'success' : 'info'">{{ row.used ? t("rules.used") : t("rules.unused") }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="70" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="openDetail(row)">{{ t("rules.detail") }}</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination class="pager" layout="total, sizes, prev, pager, next" :total="shown.length" :page-sizes="[20, 50, 100]"
        v-model:current-page="page" v-model:page-size="pageSize" />
    </div>

    <el-dialog v-model="dialog" :title="editing ? t('rules.editTitle', { code: editing.rule.rule_code }) : t('rules.create')" width="660px">
      <el-alert v-if="editing && editHint" :type="editHint === 'NEW_VERSION' ? 'warning' : 'info'" :closable="false" show-icon class="mb"
        :title="t(`rules.hint${editHint}`)" />
      <el-form label-width="120px">
        <el-form-item v-if="!editing" :label="t('rules.code')" required>
          <el-input v-model.trim="form.rule_code" :placeholder="t('rules.codeHint')" />
        </el-form-item>
        <el-form-item :label="t('rules.name')" required><el-input v-model="form.rule_name" maxlength="128" /></el-form-item>
        <!-- 所属组织：系统管理员可不选（全局规则）；普通操作员须选授权组织（只授权一个时固定为它）；建好后不能改 -->
        <el-form-item v-if="!editing" :label="t('rules.owner')" :required="!store.isAdmin">
          <FactorySelect v-model="form.factory_code" scoped :clearable="store.isAdmin" :disabled="!!store.boundFactory"
            :placeholder="store.isAdmin ? t('rules.ownerGlobal') : t('rules.owner')" />
          <div class="muted hint">{{ store.isAdmin ? t("rules.ownerHint") : t("rules.ownerOperatorHint") }}</div>
        </el-form-item>
        <!-- 不填 PI：一条规则可给多张 PI 用，用在哪些 PI 由生成页选规则决定，规则库只显示 -->
        <el-form-item :label="t('rules.prefix')" required>
          <el-input v-model.trim="form.prefix" class="sn-mono" />
          <div class="muted hint">{{ t("rules.prefixHint") }}</div>
        </el-form-item>
        <!-- 按 SN 的组成顺序：前缀 → 流水 → 米数前分隔符 + 米数 + 米数后分隔符 → 单位 → 后缀 -->
        <el-form-item :label="t('rules.base')">
          <el-radio-group v-model="form.base" @change="form.charset = ''">
            <el-radio-button v-for="b in BASES" :key="b" :value="b">{{ b }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('rules.seqLen')"><el-input-number v-model="form.seq_len" :min="1" :max="12" /></el-form-item>
        <el-form-item :label="t('rules.charset')">
          <el-input v-model.trim="form.charset" :placeholder="defaultCharset" class="sn-mono" />
          <div class="muted hint">{{ t("rules.charsetHint") }}</div>
        </el-form-item>
        <el-form-item :label="t('rules.withLength')">
          <el-switch v-model="form.with_length" />
          <span class="muted ml">{{ t("rules.withLengthHint") }}</span>
        </el-form-item>
        <template v-if="form.with_length">
          <el-form-item :label="t('rules.sepBefore')">
            <el-input v-model.trim="form.sep_before" class="w160 sn-mono" maxlength="16" :placeholder="t('common.optional')" />
            <span class="muted ml">{{ t("rules.sepHint") }}</span>
          </el-form-item>
          <el-form-item :label="t('rules.lengthPad')">
            <el-input-number v-model="form.length_pad" :min="0" :max="32" />
            <div class="muted hint">{{ t("rules.lengthPadHint") }}</div>
          </el-form-item>
          <el-form-item :label="t('rules.sepAfter')">
            <el-input v-model.trim="form.sep_after" class="w160 sn-mono" maxlength="16" :placeholder="t('common.optional')" />
          </el-form-item>
        </template>
        <el-form-item :label="t('rules.withUnit')">
          <el-switch v-model="form.with_unit" />
          <span class="muted ml">{{ t("rules.withUnitHint") }}</span>
        </el-form-item>
        <el-form-item :label="t('rules.suffix')">
          <el-input v-model.trim="form.suffix" class="sn-mono" :placeholder="t('common.optional')" />
          <div class="muted hint">{{ t("rules.suffixHint") }}</div>
        </el-form-item>
        <el-form-item v-if="form.with_length" :label="t('rules.sampleLength')">
          <el-input v-model.trim="sampleLength" class="w160" placeholder="100" />
        </el-form-item>
        <el-form-item v-if="form.with_unit" :label="t('rules.sampleUnit')">
          <el-input v-model.trim="sampleUnit" class="w160" placeholder="M" />
        </el-form-item>
        <el-form-item :label="t('rules.sample')">
          <div v-if="sample" class="samples">
            <div class="muted">{{ sample.format }}</div>
            <div class="sample-list">
              <span v-for="(s, i) in sample.samples" :key="s.seq_dec" class="sn-mono" :class="{ max: i === sample.samples.length - 1 && sample.max_seq > 5 }">{{ s.sn }}</span>
            </div>
            <div class="muted">{{ t("rules.maxSeq", { n: Number(sample.max_seq).toLocaleString() }) }}</div>
          </div>
          <span v-else class="err">{{ sampleError }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">{{ t("common.cancel") }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t("common.save") }}</el-button>
      </template>
    </el-dialog>

    <!-- 前缀详情：使用这个前缀的各 PI 占的流水；下方为所选规则的示例号（前 5 个和最大号，米数、产品单位用示例值） -->
    <el-drawer :model-value="!!detail.group" size="900px" @close="detail.group = null">
      <template #header>
        <div v-if="detail.group" class="dh">
          <b class="sn-mono">{{ detail.group.prefix }}</b>
          <el-tag size="small" effect="plain">{{ t("rules.seqFormatVal", { base: detail.group.base, n: detail.group.seq_len }) }}</el-tag>
          <span v-if="detail.group.top_seq" class="muted">{{ t("rules.topSeq") }} <span class="sn-mono">{{ detail.group.top_text }}</span></span>
        </div>
      </template>
      <template v-if="detail.group">
        <div class="sec">{{ t("rules.usageTitle") }}</div>
        <el-table v-loading="detail.usageLoading" :data="detail.usage" size="small" :empty-text="detail.usageError || t('common.empty')" class="mb">
          <el-table-column label="PI" min-width="120"><template #default="{ row }"><span class="sn-mono">{{ row.pi_no }}</span></template></el-table-column>
          <el-table-column :label="t('rules.usageRules')" min-width="140" show-overflow-tooltip><template #default="{ row }">{{ row.rules.join("、") }}</template></el-table-column>
          <el-table-column :label="t('rules.usageRange')" min-width="200">
            <template #default="{ row }">
              <template v-if="row.qty"><span class="sn-mono">{{ row.min_text }} ～ {{ row.max_text }}</span>
                <span v-if="row.has_gap" class="muted small ml">{{ t("rules.usageGap") }}</span></template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('rules.usageQty')" width="90" align="right"><template #default="{ row }">{{ Number(row.qty).toLocaleString() }}</template></el-table-column>
          <el-table-column :label="t('rules.usageTop')" width="150" align="right"><template #default="{ row }">{{ row.top_seq ? Number(row.top_seq).toLocaleString() : "—" }}</template></el-table-column>
        </el-table>
        <div class="sec">
          {{ t("rules.sampleOf") }}
          <el-radio-group v-if="detail.group.rules.length > 1" v-model="detail.ruleId" size="small" class="ml" @change="loadSample">
            <el-radio-button v-for="x in detail.group.rules" :key="x.rule.id" :value="x.rule.id">{{ x.rule.rule_code }}</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="detailRule?.version" class="muted small mb">{{ detailRule.version.format }}</div>
        <el-table v-loading="detail.loading" :data="detail.items" size="small" :empty-text="detail.error || t('common.empty')">
          <el-table-column :label="t('rules.sampleSn')" min-width="200">
            <template #default="{ row }"><span class="sn-mono" :class="{ max: row.max }">{{ row.sn }}</span></template>
          </el-table-column>
          <el-table-column :label="t('rules.prefix')" min-width="100" show-overflow-tooltip>
            <template #default="{ row }"><span class="sn-mono">{{ row.prefix }}</span></template>
          </el-table-column>
          <el-table-column :label="t('rules.seqNo')" min-width="100"><template #default="{ row }"><span class="sn-mono">{{ row.seq_text }}</span></template></el-table-column>
          <el-table-column :label="t('rules.seqDigits')" width="80"><template #default="{ row }">{{ row.seq_len }}</template></el-table-column>
          <el-table-column :label="t('rules.base')" width="64"><template #default="{ row }">{{ row.base }}</template></el-table-column>
          <el-table-column :label="t('common.length')" width="80"><template #default="{ row }">{{ row.length || "—" }}</template></el-table-column>
          <el-table-column :label="t('common.unit')" width="80"><template #default="{ row }">{{ row.unit || "—" }}</template></el-table-column>
          <el-table-column :label="t('rules.suffixCol')" min-width="90" show-overflow-tooltip>
            <template #default="{ row }"><span v-if="row.suffix" class="sn-mono">{{ row.suffix }}</span><span v-else class="muted">—</span></template>
          </el-table-column>
        </el-table>
        <div class="muted small mt">{{ t("rules.detailNote") }}</div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import PageHead from "@/components/PageHead.vue";
import FactorySelect from "@/components/FactorySelect.vue";
import PiTags from "@/components/PiTags.vue";
import { BASES, CHARSETS } from "@/constants";
import { useFactories } from "@/composables/useFactories";
import { useAuthStore } from "@/stores/auth";

const { t } = useI18n();
const store = useAuthStore();
const { factories } = useFactories();
const factoryName = (code: string) => {
  const f = factories.value.find((x) => x.factory_code === code);
  return f ? `${f.factory_name}（${code}）` : code;
};
/** 按前缀归档的分组（GET /rules/prefixes）：每组 prefix、base、seq_len、rules[]、pis、selected_pis、top_seq… */
const rows = ref<any[]>([]);
const loading = ref(false);
const q = ref("");
const piFilter = ref("");
const page = ref(1);
const pageSize = ref(20);
const dialog = ref(false);
const editing = ref<any>(null);
const saving = ref(false);
const sample = ref<any>(null);
const sampleError = ref("");
const sampleLength = ref("");
const sampleUnit = ref("");
/** 规则库流水位数默认 5（自定义规则默认 4，在生成页）。 */
const EMPTY = {
  rule_code: "", rule_name: "", factory_code: "", prefix: "", suffix: "", base: 10, seq_len: 5, charset: "",
  with_length: true, length_pad: 0, sep_before: "", sep_after: "", with_unit: false,
};
const form = reactive({ ...EMPTY });
const defaultCharset = computed(() => CHARSETS[form.base]);
/** 各组用到的 PI（生成过号的与已选未生成的），供 PI 筛选。 */
const groupPis = (g: any): string[] => [...g.pis, ...g.selected_pis];
const piValues = computed(() => [...new Set(rows.value.flatMap(groupPis))].sort());
const shown = computed(() => (piFilter.value ? rows.value.filter((g) => groupPis(g).includes(piFilter.value)) : rows.value));
/** 默认分页列出全部前缀（前端分页）；改 PI 筛选回第一页，条数变少时页码不越界。 */
const paged = computed(() => shown.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value));
watch(piFilter, () => (page.value = 1));
watch(() => shown.value.length, (n) => (page.value = Math.min(page.value, Math.max(1, Math.ceil(n / pageSize.value)))));
/** 编辑时提示：格式不变 / 就地修改（当前版本没生成过号）/ 另出一版（生成过号）。 */
const editHint = computed(() => {
  const c = editing.value?.current;
  if (!c) return "";
  const charset = form.charset || CHARSETS[form.base];
  // 不带米数时补 0 位数、分隔符不起作用（后端统一存空）
  const len = form.with_length;
  const changed = c.prefix !== form.prefix || c.suffix !== form.suffix || c.base !== form.base || c.seq_len !== form.seq_len
    || c.charset !== charset || !!c.with_length !== len || !!c.with_unit !== form.with_unit
    || (c.length_pad || 0) !== (len ? form.length_pad || 0 : 0)
    || (c.length_sep_before || "") !== (len ? form.sep_before : "") || (c.length_sep_after || "") !== (len ? form.sep_after : "");
  if (!changed) return c.used ? "USED" : "";
  return c.used ? "NEW_VERSION" : "IN_PLACE";
});

async function load() {
  loading.value = true;
  try {
    rows.value = await api.rulePrefixes({ q: q.value });
  } finally {
    loading.value = false;
  }
}

function search() {
  page.value = 1;
  load();
}

function openCreate() {
  editing.value = null;
  Object.assign(form, EMPTY, { factory_code: store.boundFactory });
  dialog.value = true;
}

/** 编辑一条规则（按它的当前版本；规则换过前缀时当前版本可能在别的前缀下）。 */
function openEdit(row: any) {
  editing.value = row;
  const c = row.current;
  Object.assign(form, {
    rule_name: row.rule.rule_name,
    prefix: c.prefix, suffix: c.suffix, base: c.base, seq_len: c.seq_len, with_length: !!c.with_length,
    length_pad: c.length_pad || 0, sep_before: c.length_sep_before || "", sep_after: c.length_sep_after || "", with_unit: !!c.with_unit,
    charset: c.charset === CHARSETS[c.base] ? "" : c.charset,
  });
  dialog.value = true;
}

/** 版本的编码参数（与编辑表单提交的一致），用于取示例号。 */
const specOf = (c: any) => ({
  prefix: c.prefix, suffix: c.suffix, base: c.base, seq_len: c.seq_len, charset: c.charset, with_length: !!c.with_length,
  length_pad: c.length_pad || 0, sep_before: c.length_sep_before || "", sep_after: c.length_sep_after || "", with_unit: !!c.with_unit,
});

/** 前缀详情：各 PI 占的流水（GET /rules/prefixes/usage）与所选规则的示例号（前 5 个和最大号）。 */
const detail = reactive({
  group: null as any, ruleId: 0, loading: false, items: [] as any[], error: "",
  usage: [] as any[], usageLoading: false, usageError: "",
});
const detailRule = computed(() => detail.group?.rules.find((x: any) => x.rule.id === detail.ruleId) ?? null);
async function openDetail(g: any) {
  Object.assign(detail, { group: g, ruleId: g.rules[0]?.rule.id ?? 0, usage: [], usageError: "" });
  loadSample();
  detail.usageLoading = true;
  try {
    const r: any = await api.rulePrefixUsage(g.prefix);
    if (detail.group === g) detail.usage = r.pis;
  } catch (e: any) {
    if (detail.group === g) detail.usageError = e?.message || "";
  } finally {
    if (detail.group === g) detail.usageLoading = false;
  }
}

/** 示例号：所选规则在这个前缀上的版本，各行拆出前缀、流水号、米数、产品单位、后缀。 */
async function loadSample() {
  const x = detailRule.value;
  Object.assign(detail, { items: [], error: "" });
  const c = x?.version;
  if (!c) return;
  detail.loading = true;
  try {
    const r: any = await api.previewRule(specOf(c));
    if (detailRule.value !== x) return; // 等结果时已改看别的规则
    const last = r.samples.length - 1;
    detail.items = r.samples.map((s: any, i: number) => ({
      ...s, prefix: c.prefix, suffix: c.suffix, base: c.base, seq_len: c.seq_len, max: i === last && r.max_seq > 5,
      length: c.with_length ? r.sample_length : "", unit: c.with_unit ? r.sample_unit : "",
    }));
  } catch (e: any) {
    if (detailRule.value === x) detail.error = e?.message || "";
  } finally {
    if (detailRule.value === x) detail.loading = false;
  }
}

const spec = () => ({
  prefix: form.prefix, suffix: form.suffix, base: form.base, seq_len: form.seq_len, charset: form.charset, with_length: form.with_length,
  length_pad: form.length_pad || 0, sep_before: form.sep_before, sep_after: form.sep_after, with_unit: form.with_unit,
});

let sampleTimer: number | undefined;
watch(() => [form.prefix, form.suffix, form.base, form.seq_len, form.charset, form.with_length, form.length_pad, form.sep_before,
  form.sep_after, form.with_unit, sampleLength.value, sampleUnit.value, dialog.value], () => {
  window.clearTimeout(sampleTimer);
  if (!dialog.value) return;
  sampleTimer = window.setTimeout(async () => {
    try {
      sample.value = await api.previewRule({ ...spec(), length: sampleLength.value, unit: sampleUnit.value });
      sampleError.value = "";
    } catch (e: any) {
      sample.value = null;
      sampleError.value = e?.message || "";
    }
  }, 250);
});

async function save() {
  saving.value = true;
  try {
    if (editing.value) {
      // 不传 PI：规则原有的分类与 PI 不变
      const r: any = await api.updateRule(editing.value.rule.id, { rule_name: form.rule_name, ...spec() });
      ElMessage.success(t(`rules.result${r.action}`, { v: r.rule?.rule?.current_version }));
    } else {
      await api.createRule({ rule_code: form.rule_code, rule_name: form.rule_name, factory_code: form.factory_code || null, ...spec() });
      ElMessage.success(t("common.saved"));
    }
    dialog.value = false;
    await load();
  } finally {
    saving.value = false;
  }
}

/** 修改、删除：系统管理员不限；普通操作员只能改授权组织的规则，全局规则只能选用（后端同样校验）。 */
const editable = (row: any) => store.isAdmin || (!!row.rule.factory_code && store.canWrite(row.rule.factory_code));
/** 只能删从没生成过号、也没有 PI 使用（选定或已归属）的规则（后端同样校验）。 */
const deletable = (row: any) => !row.rule_used && !row.pis.length && !row.bindings.pi.length;

async function remove(row: any) {
  const r = row.rule;
  await ElMessageBox.confirm(t("rules.deleteConfirm", { code: r.rule_code, name: r.rule_name }), t("common.delete"), {
    type: "warning", confirmButtonText: t("common.delete"), cancelButtonText: t("common.cancel"), confirmButtonClass: "el-button--danger",
  });
  await api.deleteRule(r.id);
  ElMessage.success(t("rules.deleted", { code: r.rule_code }));
  await load();
}

onMounted(load);
</script>

<style scoped>
.ml { margin-left: 6px; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 10px; }
.small { font-size: 12px; }
.dh { display: flex; align-items: center; gap: 8px; }
.sec { font-weight: 600; margin: 4px 0 8px; display: flex; align-items: center; }
.inner { margin: 4px 0 8px 24px; width: calc(100% - 24px); }
.max { color: var(--el-color-warning); }
.hint { width: 100%; line-height: 1.5; margin-top: 2px; }
.w160 { width: 160px; }
.samples { display: flex; flex-direction: column; gap: 4px; }
.sample-list { display: flex; flex-wrap: wrap; gap: 4px 12px; }
.sample-list .max { color: var(--el-color-warning); }
.err { color: var(--el-color-danger); font-size: 12px; }
</style>
