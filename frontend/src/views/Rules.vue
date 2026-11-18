<template>
  <div class="page page--fill">
    <!-- 页头：说明收进标题旁的问号，悬停显示 -->
    <div class="page__head">
      <h2 class="page__title title-q">
        {{ t("rules.title") }}
        <el-tooltip placement="bottom-start" :content="t('rules.desc')" popper-class="rules-desc-pop">
          <el-icon class="q"><QuestionFilled /></el-icon>
        </el-tooltip>
      </h2>
      <div class="page__actions">
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon><span>{{ t("rules.create") }}</span></el-button>
      </div>
    </div>
    <!-- 左右分栏：左侧前缀清单（一个前缀一项），右侧所选前缀的 SN 组成、规则、使用的 PI、示例号 -->
    <div class="split fill">
      <div class="surface side">
        <el-input v-model="q" clearable :placeholder="t('rules.search')" prefix-icon="Search" @keyup.enter="search" @clear="search" />
        <div class="side-filter">
          <el-select v-model="piFilter" filterable clearable :placeholder="t('rules.filterPi')" class="pi-sel">
            <el-option v-for="v in piValues" :key="v" :value="v" :label="v" />
          </el-select>
          <el-segmented v-model="usedFilter" :options="usedOptions" size="default" />
        </div>
        <div v-loading="loading" class="plist">
          <div v-for="g in paged" :key="g.prefix" class="pitem" :class="{ on: cur?.prefix === g.prefix }" @click="pick(g)">
            <div class="pitem-top">
              <b class="sn-mono pfx">{{ g.prefix }}</b>
              <span class="dot" :class="g.used ? 'ok' : ''">{{ g.used ? t("rules.used") : t("rules.unused") }}</span>
            </div>
            <div class="pitem-meta">
              <span>{{ t("rules.seqFormatVal", { base: g.base, n: g.seq_len }) }}</span>
              <span class="sep">·</span><span>{{ t("rules.ruleCountVal", { n: g.rules.length }) }}</span>
              <span class="sep">·</span><span>{{ t("rules.piCountVal", { n: groupPis(g).length }) }}</span>
            </div>
            <div class="pitem-org">{{ orgText(g) }}</div>
          </div>
          <el-empty v-if="!loading && !shown.length" :image-size="72" :description="t('common.empty')" />
        </div>
        <el-pagination v-if="shown.length > pageSize" small class="side-pager" layout="prev, pager, next" :total="shown.length"
          v-model:current-page="page" :page-size="pageSize" />
      </div>

      <div class="surface main">
        <el-empty v-if="!cur" :image-size="96" :description="t('rules.pickPrefix')" />
        <template v-else>
          <div class="mhead">
            <div class="mtitle">
              <span class="sn-mono big">{{ cur.prefix }}</span>
              <el-tag effect="plain">{{ t("rules.seqFormatVal", { base: cur.base, n: cur.seq_len }) }}</el-tag>
              <el-tag :type="cur.used ? 'success' : 'info'" effect="light">{{ cur.used ? t("rules.used") : t("rules.unused") }}</el-tag>
              <span class="muted">{{ orgText(cur) }}</span>
            </div>
            <!-- 流水上限自动突破：按前缀统一设置（作用于这个前缀下的全部 PI）；已突破后不能关闭 -->
            <el-tooltip placement="bottom" :content="t('rules.seqGrowTip')">
              <span class="grow">
                {{ t("rules.seqGrow") }}
                <el-switch :model-value="cur.seq_grow !== false" :loading="growSaving" @change="(v: any) => setGrow(!!v)" />
              </span>
            </el-tooltip>
          </div>

          <div class="stats">
            <div class="stat"><div class="l">{{ t("rules.topSeq") }}</div><div class="v sn-mono">{{ cur.top_seq ? cur.top_text : "—" }}</div></div>
            <div class="stat"><div class="l">{{ t("rules.statQty") }}</div><div class="v num">{{ usedQty.toLocaleString() }}</div></div>
            <div class="stat"><div class="l">{{ t("rules.usageTitle") }}</div><div class="v num">{{ groupPis(cur).length }}</div></div>
            <div class="stat"><div class="l">{{ t("rules.ruleCount") }}</div><div class="v num">{{ cur.rules.length }}</div></div>
          </div>

          <!-- SN 组成：所选规则的第一个示例号按组成拆成色块，下方注明各段含义 -->
          <div class="sec">
            <span>{{ t("rules.composition") }}</span>
            <el-radio-group v-if="cur.rules.length > 1" v-model="detail.ruleId" size="small" @change="loadSample">
              <el-radio-button v-for="x in cur.rules" :key="x.rule.id" :value="x.rule.id">{{ x.rule.rule_code }}</el-radio-button>
            </el-radio-group>
          </div>
          <div v-loading="detail.loading" class="compose">
            <template v-if="parts.length">
              <div class="parts">
                <div v-for="(p, i) in parts" :key="i" class="part" :class="p.k">
                  <span class="sn-mono">{{ p.text }}</span>
                  <small>{{ p.label }}</small>
                </div>
              </div>
              <div class="samples">
                <span v-for="x in detail.items" :key="x.seq_dec" class="sn-mono chip" :class="{ max: x.max }">{{ x.sn }}</span>
              </div>
              <div class="muted">{{ t("rules.detailNote") }}</div>
            </template>
            <span v-else-if="detail.error" class="err">{{ detail.error }}</span>
          </div>

          <div class="sec">{{ t("rules.rulesTitle") }}</div>
          <el-table :data="cur.rules" size="small" :row-key="(x: any) => x.rule.id">
            <!-- 只列规则编码（唯一、建好后不能改，生成页与「使用的 PI」都按编码）；名称悬停显示，在修改弹窗里改 -->
            <el-table-column :label="t('rules.code')" min-width="140">
              <template #default="{ row }"><span class="sn-mono" :title="row.rule.rule_name">{{ row.rule.rule_code }}</span></template>
            </el-table-column>
            <el-table-column :label="t('rules.format')" min-width="260">
              <template #default="{ row }">
                <span>{{ row.version?.format }}</span>
                <el-tag v-if="!row.is_current_prefix && row.current" size="small" type="warning" effect="plain" class="ml">
                  {{ t("rules.otherPrefix", { prefix: row.current.prefix }) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('rules.version')" width="64"><template #default="{ row }">v{{ row.rule.current_version }}</template></el-table-column>
            <el-table-column :label="t('rules.owner')" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">{{ row.rule.factory_code ? factoryName(row.rule.factory_code) : t("rules.ownerGlobal") }}</template>
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

          <div class="sec">
            <span>{{ t("rules.usageTitle") }}</span>
            <span v-if="cur.selected_pis.length" class="muted">{{ t("rules.pisSelected") }}：
              <el-tag v-for="p in cur.selected_pis" :key="p" size="small" type="info" effect="plain" class="sn-mono ml">{{ p }}</el-tag></span>
          </div>
          <el-table v-loading="detail.usageLoading" :data="detail.usage" size="small" :empty-text="detail.usageError || t('common.empty')">
            <el-table-column label="PI" min-width="130"><template #default="{ row }"><span class="sn-mono">{{ row.pi_no }}</span></template></el-table-column>
            <el-table-column :label="t('rules.usageRules')" min-width="120" show-overflow-tooltip><template #default="{ row }">{{ row.rules.join("、") }}</template></el-table-column>
            <el-table-column :label="t('rules.usageRange')" min-width="220">
              <template #default="{ row }">
                <template v-if="row.qty"><span class="sn-mono">{{ row.min_text }} ～ {{ row.max_text }}</span>
                  <el-tag v-if="row.has_gap" size="small" type="warning" effect="plain" class="ml">{{ t("rules.usageGap") }}</el-tag></template>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('rules.usageQty')" width="100" align="right"><template #default="{ row }"><span class="num">{{ Number(row.qty).toLocaleString() }}</span></template></el-table-column>
            <el-table-column :label="t('rules.usageTop')" width="120" align="right"><template #default="{ row }"><span class="num">{{ row.top_seq ? Number(row.top_seq).toLocaleString() : "—" }}</span></template></el-table-column>
          </el-table>
        </template>
      </div>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import FactorySelect from "@/components/FactorySelect.vue";
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
/** 状态筛选（页面上筛，接口不变）：全部 / 生成过号 / 未使用 */
const usedFilter = ref<"ALL" | "USED" | "UNUSED">("ALL");
const page = ref(1);
const pageSize = 20;
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
const byPi = computed(() => (piFilter.value ? rows.value.filter((g) => groupPis(g).includes(piFilter.value)) : rows.value));
const usedOptions = computed(() => {
  const n = byPi.value.filter((g) => g.used).length;
  return [
    { value: "ALL", label: `${t("common.all")} ${byPi.value.length}` },
    { value: "USED", label: `${t("rules.used")} ${n}` },
    { value: "UNUSED", label: `${t("rules.unused")} ${byPi.value.length - n}` },
  ];
});
const shown = computed(() => (usedFilter.value === "ALL" ? byPi.value : byPi.value.filter((g) => g.used === (usedFilter.value === "USED"))));
/** 左侧清单前端分页；改筛选回第一页，条数变少时页码不越界。 */
const paged = computed(() => shown.value.slice((page.value - 1) * pageSize, page.value * pageSize));
watch([piFilter, usedFilter], () => (page.value = 1));
watch(() => shown.value.length, (n) => (page.value = Math.min(page.value, Math.max(1, Math.ceil(n / pageSize)))));
/** 生产组织：前缀下各规则的所属组织，没有的为「全局」。 */
const orgText = (g: any) => g.factories.map((f: string | null) => (f ? factoryName(f) : t("rules.ownerGlobal"))).join("、");
/** 右侧所选前缀：按前缀记住，重新加载后仍选中；不在当前筛选结果里时改选第一项。 */
const curPrefix = ref("");
const cur = computed(() => shown.value.find((g) => g.prefix === curPrefix.value) ?? null);
const pick = (g: any) => (curPrefix.value = g.prefix);
watch(paged, (list) => {
  if (!cur.value && list.length) curPrefix.value = list[0].prefix;
});
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
  ruleId: 0, loading: false, items: [] as any[], error: "", parts: [] as any[],
  usage: [] as any[], usageLoading: false, usageError: "",
});
const detailRule = computed(() => cur.value?.rules.find((x: any) => x.rule.id === detail.ruleId) ?? null);
/** 已生成枚数：各 PI 占的流水枚数之和。 */
const usedQty = computed(() => detail.usage.reduce((n: number, x: any) => n + Number(x.qty || 0), 0));
const parts = computed(() => detail.parts);
const growSaving = ref(false);
/** 改前缀的流水上限自动突破开关（PUT /rules/prefixes/grow） */
async function setGrow(v: boolean) {
  const g = cur.value;
  if (!g) return;
  growSaving.value = true;
  try {
    await api.rulePrefixGrow({ prefix: g.prefix, seq_grow: v });
    g.seq_grow = v;
    ElMessage.success(t("common.saved"));
    load();
  } finally {
    growSaving.value = false;
  }
}
/** 切换前缀时取使用情况与示例号（同一前缀重新加载列表时规则有变也重取示例号）。 */
watch(curPrefix, async (prefix) => {
  Object.assign(detail, { ruleId: cur.value?.rules[0]?.rule.id ?? 0, usage: [], usageError: "" });
  loadSample();
  if (!prefix) return;
  detail.usageLoading = true;
  try {
    const r: any = await api.rulePrefixUsage(prefix);
    if (curPrefix.value === prefix) detail.usage = r.pis;
  } catch (e: any) {
    if (curPrefix.value === prefix) detail.usageError = e?.message || "";
  } finally {
    if (curPrefix.value === prefix) detail.usageLoading = false;
  }
});
watch(() => cur.value?.rules, (list) => {
  if (!list) return;
  if (!list.some((x: any) => x.rule.id === detail.ruleId)) detail.ruleId = list[0]?.rule.id ?? 0;
  loadSample();
});

/** 第一个示例号按组成拆段：前缀 / 流水 / 米数（含前后分隔符）/ 单位 / 后缀，只拆示例号本身，不另行拼号。 */
function splitParts(c: any, s: any, unit: string) {
  const out = [{ k: "prefix", label: t("rules.prefix"), text: c.prefix }, { k: "seq", label: t("rules.seqNo"), text: s.seq_text }];
  let mid = s.sn.slice(c.prefix.length + s.seq_text.length, s.sn.length - (c.suffix || "").length);
  if (c.with_unit && unit && mid.endsWith(unit)) mid = mid.slice(0, mid.length - unit.length);
  else unit = "";
  if (c.with_length && mid) out.push({ k: "length", label: t("common.length"), text: mid });
  if (unit) out.push({ k: "unit", label: t("common.unit"), text: unit });
  if (c.suffix) out.push({ k: "suffix", label: t("rules.suffixCol"), text: c.suffix });
  return out;
}

/** 示例号：所选规则在这个前缀上的版本（前 5 个和最大号，米数、产品单位用示例值）。 */
async function loadSample() {
  const x = detailRule.value;
  Object.assign(detail, { items: [], error: "", parts: [] });
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
    if (r.samples.length) detail.parts = splitParts(c, r.samples[0], c.with_unit ? r.sample_unit || "" : "");
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
.title-q { display: flex; align-items: center; gap: 6px; }
.q { font-size: 16px; color: var(--app-muted); cursor: help; }
.q:hover { color: var(--el-color-primary); }
.mb { margin-bottom: 12px; }
.hint { width: 100%; line-height: 1.5; margin-top: 2px; }
.w160 { width: 160px; }
.samples { display: flex; flex-wrap: wrap; gap: 6px 8px; }
.max { color: var(--el-color-warning); }
.err { color: var(--el-color-danger); font-size: 12px; }
/* 左右分栏：左侧前缀清单固定宽度，右侧详情自适应；窄屏上下排列 */
.split { display: flex; flex-direction: row !important; gap: 16px; align-items: stretch; }
.side { width: 320px; flex: none; display: flex; flex-direction: column; gap: 10px; padding: 14px; min-height: 0; }
.side-filter { display: flex; flex-direction: column; gap: 8px; }
.pi-sel { width: 100%; }
.side-filter :deep(.el-segmented) { width: 100%; }
.plist { flex: 1; min-height: 120px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; margin: 0 -4px; padding: 0 4px; }
.pitem { border: 1px solid var(--app-line); border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: border-color .15s, background .15s; }
.pitem:hover { border-color: var(--el-color-primary-light-5); }
.pitem.on { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); box-shadow: inset 3px 0 0 var(--el-color-primary); }
.pitem-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.pfx { font-size: 15px; word-break: break-all; }
.dot { font-size: 12px; color: var(--app-muted); white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; }
.dot::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--el-color-info-light-5); }
.dot.ok { color: var(--el-color-success); }
.dot.ok::before { background: var(--el-color-success); }
.pitem-meta { margin-top: 4px; font-size: 12px; color: var(--app-text); }
.pitem-meta .sep { margin: 0 5px; color: var(--app-muted); }
.pitem-org { margin-top: 2px; font-size: 12px; color: var(--app-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.side-pager { justify-content: center; margin-top: 0; }
.main { flex: 1; min-width: 0; overflow-y: auto; display: flex; flex-direction: column; }
.mhead { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding-bottom: 14px; border-bottom: 1px solid var(--app-line); }
.mtitle { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.big { font-size: 22px; font-weight: 700; }
.grow { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--app-text); }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.stat { border: 1px solid var(--app-line); border-radius: 8px; padding: 10px 14px; }
.stat .l { font-size: 12px; color: var(--app-muted); }
.stat .v { font-size: 20px; font-weight: 600; margin-top: 2px; }
.sec { font-weight: 600; font-size: 14px; margin: 22px 0 10px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.compose { display: flex; flex-direction: column; gap: 12px; min-height: 60px; }
.parts { display: flex; flex-wrap: wrap; gap: 3px; }
.part { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 8px 12px 6px; border-radius: 6px; }
.part .sn-mono { font-size: 18px; font-weight: 600; }
.part small { font-size: 11px; color: var(--app-muted); }
.part.prefix { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.part.seq { background: var(--el-color-success-light-9); color: var(--el-color-success-dark-2); }
.part.length { background: var(--el-color-warning-light-9); color: var(--el-color-warning-dark-2); }
.part.unit { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.part.suffix { background: var(--el-fill-color); color: var(--app-text); }
.chip { padding: 2px 8px; border-radius: 4px; background: var(--el-fill-color-light); font-size: 13px; }
.chip.max { background: var(--el-color-warning-light-9); }
@media (max-width: 1199px) {
  .split { flex-direction: column !important; }
  .side { width: auto; }
  .plist { max-height: 320px; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
<style>
/* 问号说明的气泡（挂在 body 下，不能用 scoped） */
.rules-desc-pop { max-width: 520px; line-height: 1.7; }
</style>
