<template>
  <div class="page page--fill">
    <!-- 页头：说明收进标题旁的问号，悬停显示 -->
    <PageHead :title="t('rules.title')" :desc="t('rules.desc')">
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon><span>{{ t("rules.create") }}</span></el-button>
    </PageHead>
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
          <!-- 一个前缀一行：前缀（粗体）+ 灰色「规则编码 · 被 N 个 PI 使用」+ 绿色「第一 SN / 第二 SN」 -->
          <div v-for="g in paged" :key="g.prefix" class="pitem" :class="{ on: cur?.prefix === g.prefix }" tabindex="0"
            @click="pick(g)" @keyup.enter="pick(g)">
            <b class="sn-mono pfx" :title="g.prefix">{{ g.prefix }}</b>
            <span class="meta" :title="ruleCodes(g)">
              <span class="sn-mono">{{ ruleCodes(g) }}</span> · {{ t("rules.usedByPis", { n: groupPis(g).length }) }}
            </span>
            <span class="slot">{{ g.role === 2 ? t("rules.slotSecond") : t("rules.slotFirst") }}</span>
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
              <el-tag v-if="cur.role === 2" type="warning" effect="plain">{{ t("rules.roleSecond") }}</el-tag>
              <el-tooltip v-if="cur.inc" placement="bottom" :content="t('generate.rule.incTip')">
                <el-tag type="success" effect="plain">{{ incTagText(cur.inc) }}</el-tag>
              </el-tooltip>
              <!-- 识别码适用客户：规则库页面保存即生效（生成页识别码组件里改的，本 PI 生成成功才生效） -->
              <el-button v-if="cur.inc" link type="primary" @click="openCust">{{ t("rules.incCustomersEdit") }}</el-button>
              <span class="muted">{{ orgText(cur) }}</span>
            </div>
            <!-- 流水上限自动突破：按前缀统一设置（作用于这个前缀下的全部 PI）；已突破后不能关闭。
                 自增前缀下各 PI 的号段各自设置（选规则时定下），这里不显示 -->
            <el-tooltip v-if="!cur.inc" placement="bottom" :content="t('rules.seqGrowTip')">
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
                <span v-for="x in detail.items" :key="x.seq_dec" class="sn-mono chip">{{ x.sn }}</span>
                <HelpTip :content="t('rules.detailNote')" />
              </div>
            </template>
            <span v-else-if="detail.error" class="err">{{ detail.error }}</span>
          </div>

          <div class="sec">{{ t("rules.rulesTitle") }}</div>
          <el-table :data="cur.rules" size="small" :row-key="(x: any) => x.rule.id">
            <!-- 编码 ｜ 前缀 ｜ 进制 ｜ 后缀 ｜ 开始 SN ｜ 当前最大 SN ｜ 操作（规则在这个前缀上的版本；开始 SN、当前最大 SN
                 为号池里挂在这条规则上的号，还没生成过号时开始 SN 为从流水 1 起的示例、置灰）。
                 规则的当前版本已换到别的前缀时编码后标出 -->
            <el-table-column :label="t('generate.rule.listCol.code')" min-width="140">
              <template #default="{ row }">
                <span class="sn-mono">{{ row.rule.rule_code }}</span>
                <el-tag v-if="!row.is_current_prefix && row.current" size="small" type="warning" effect="plain" class="ml">
                  {{ t("rules.otherPrefix", { prefix: row.current.prefix }) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('rules.prefix')" min-width="90">
              <template #default="{ row }"><span class="sn-mono">{{ cur.inc ? cur.prefix : row.version?.prefix }}</span></template>
            </el-table-column>
            <el-table-column :label="t('rules.base')" width="80">
              <template #default="{ row }">{{ row.version?.base ? t("generate.rule.baseN", { n: row.version.base }) : "—" }}</template>
            </el-table-column>
            <el-table-column :label="t('rules.suffixCol')" min-width="80">
              <template #default="{ row }"><span class="sn-mono">{{ row.version?.suffix || "—" }}</span></template>
            </el-table-column>
            <el-table-column :label="t('generate.rule.listCol.start')" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="sn-mono" :class="{ muted: detail.ruleSns[row.rule.id]?.start_sample }"
                  :title="detail.ruleSns[row.rule.id]?.start_sample ? t('generate.rule.listCol.startSample') : undefined">
                  {{ detail.ruleSns[row.rule.id]?.start_sn || "—" }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('generate.rule.listCol.max')" min-width="150" show-overflow-tooltip>
              <template #default="{ row }"><span class="sn-mono">{{ detail.ruleSns[row.rule.id]?.max_sn || "—" }}</span></template>
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
            <!-- 自增前缀：本 PI 的号段（前缀 + 自增值） -->
            <el-table-column v-if="cur.inc" :label="t('rules.incSegment')" min-width="110">
              <template #default="{ row }"><span class="sn-mono">{{ row.seq_prefix || "—" }}</span></template>
            </el-table-column>
            <!-- 识别码号段状态：使用中 / 客户已移出（号段上还有号）/ 已释放（可给别的 PI 改选） -->
            <el-table-column v-if="cur.inc" :label="t('rules.incStatus')" min-width="110">
              <template #default="{ row }">
                <el-tag v-if="incStatus(row)" size="small" effect="plain" :type="incStatus(row) === 'USED' ? 'success' : incStatus(row) === 'RELEASED' ? 'info' : 'warning'">
                  {{ t(`rules.incStatusOf.${incStatus(row)}`) }}</el-tag>
              </template>
            </el-table-column>
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
        <!-- 所属组织：系统管理员可不选（全局规则）；普通操作员须选授权组织（只授权一个时固定为它）；建好后不能改 -->
        <el-form-item v-if="!editing" :required="!store.isAdmin">
          <template #label>{{ t("rules.owner") }}<HelpTip :content="store.isAdmin ? t('rules.ownerHint') : t('rules.ownerOperatorHint')" /></template>
          <FactorySelect v-model="form.factory_code" scoped :clearable="store.isAdmin" :disabled="!!store.boundFactory"
            :placeholder="store.isAdmin ? t('rules.ownerGlobal') : t('rules.owner')" />
        </el-form-item>
        <!-- 不填 PI：一条规则可给多张 PI 用，用在哪些 PI 由生成页选规则决定，规则库只显示 -->
        <!-- 一个前缀一条规则：已用于生成的规则前缀、进制、流水位数、字符表锁定（要换须新建规则） -->
        <!-- 前缀规则说明收进问号；「已用于生成、格式锁定」是当前状态，仍直接显示 -->
        <el-form-item required>
          <template #label>{{ t("rules.prefix") }}<HelpTip :content="t('rules.prefixHint')" /></template>
          <el-input v-model.trim="form.prefix" class="sn-mono" :disabled="formatLocked" />
          <div v-if="formatLocked" class="muted hint">{{ t("rules.formatLocked") }}</div>
        </el-form-item>
        <!-- 按 SN 的组成顺序：前缀 → 流水 → 米数前分隔符 + 米数 + 米数后分隔符 → 单位 → 后缀 -->
        <el-form-item :label="t('rules.base')">
          <el-radio-group v-model="form.base" :disabled="formatLocked" @change="form.charset = ''">
            <el-radio-button v-for="b in BASES" :key="b" :value="b">{{ b }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('rules.seqLen')"><el-input-number v-model="form.seq_len" :min="1" :max="12" :disabled="formatLocked" /></el-form-item>
        <el-form-item>
          <template #label>{{ t("rules.charset") }}<HelpTip :content="t('rules.charsetHint')" /></template>
          <el-input v-model.trim="form.charset" :placeholder="defaultCharset" class="sn-mono" :disabled="formatLocked" />
        </el-form-item>
        <el-form-item>
          <template #label>{{ t("rules.withLength") }}<HelpTip :content="t('rules.withLengthHint')" /></template>
          <el-switch v-model="form.with_length" />
        </el-form-item>
        <template v-if="form.with_length">
          <el-form-item>
            <template #label>{{ t("rules.sepBefore") }}<HelpTip :content="t('rules.sepHint')" /></template>
            <el-input v-model.trim="form.sep_before" class="w160 sn-mono" maxlength="16" :placeholder="t('common.optional')" />
          </el-form-item>
          <el-form-item>
            <template #label>{{ t("rules.lengthPad") }}<HelpTip :content="t('rules.lengthPadHint')" /></template>
            <el-input-number v-model="form.length_pad" :min="0" :max="32" />
          </el-form-item>
          <el-form-item>
            <template #label>{{ t("rules.sepAfter") }}<HelpTip :content="t('rules.sepHint')" /></template>
            <el-input v-model.trim="form.sep_after" class="w160 sn-mono" maxlength="16" :placeholder="t('common.optional')" />
          </el-form-item>
        </template>
        <el-form-item>
          <template #label>{{ t("rules.withUnit") }}<HelpTip :content="t('rules.withUnitHint')" /></template>
          <el-switch v-model="form.with_unit" />
        </el-form-item>
        <!-- 附件项次（前缀与流水之间，在生成页「选规则 → 自定义」里配置）：这里只显示、保存时原样带上 -->
        <el-form-item v-if="form.attach_width" :label="t('generate.rule.part.ATTACH')">
          <span class="sn-mono">{{ t("generate.rule.attachUsed", { width: form.attach_width }) }}{{ form.attach_tail ? ` + ${form.attach_tail}` : "" }}</span>
        </el-form-item>
        <el-form-item>
          <template #label>{{ t("rules.suffix") }}<HelpTip :content="t('rules.suffixHint')" /></template>
          <el-input v-model.trim="form.suffix" class="sn-mono" :placeholder="t('common.optional')" />
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

    <!-- 识别码适用客户：只能选已同步的订单客户；授权范围外的客户可见、不能增删（后端校验）；删除前列出受影响的 PI -->
    <el-dialog v-model="cust.open" :title="t('rules.incCustomersTitle', { prefix: cur?.prefix ?? '' })" width="560px">
      <el-select v-model="cust.list" multiple filterable remote :remote-method="searchCust" :loading="cust.searching"
        class="full" :placeholder="t('rules.incCustomersPh')">
        <el-option v-for="c in custOptions" :key="c" :label="c" :value="c" />
      </el-select>
      <div class="muted hint">{{ t("rules.incCustomersHint") }}</div>
      <template #footer>
        <el-button @click="cust.open = false">{{ t("common.cancel") }}</el-button>
        <el-button type="primary" :loading="cust.saving" :disabled="!cust.list.length" @click="saveCust">{{ t("common.save") }}</el-button>
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
import HelpTip from "@/components/HelpTip.vue";
import PageHead from "@/components/PageHead.vue";
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
  rule_code: "", factory_code: "", prefix: "", suffix: "", base: 10, seq_len: 5, charset: "",
  with_length: true, length_pad: 0, sep_before: "", sep_after: "", with_unit: false, attach_width: 0, attach_tail: "",
};
const form = reactive({ ...EMPTY });
const defaultCharset = computed(() => CHARSETS[form.base]);
/** 各组用到的 PI（生成过号的与已选未生成的），供 PI 筛选。 */
const groupPis = (g: any): string[] => [...g.pis, ...g.selected_pis];
const piValues = computed(() => [...new Set(rows.value.flatMap(groupPis))].sort());
const byPi = computed(() => (piFilter.value ? rows.value.filter((g) => groupPis(g).includes(piFilter.value)) : rows.value));
const usedOptions = computed(() => [
  { value: "ALL", label: t("common.all") },
  { value: "USED", label: t("rules.used") },
  { value: "UNUSED", label: t("rules.unused") },
]);
/** 左侧清单的规则编码（一个前缀通常一条；换过前缀的规则也列在旧前缀下） */
const ruleCodes = (g: any) => g.rules.map((x: any) => x.rule.rule_code).join("、");
const shown = computed(() => (usedFilter.value === "ALL" ? byPi.value : byPi.value.filter((g) => g.used === (usedFilter.value === "USED"))));
/** 左侧清单前端分页；改筛选回第一页，条数变少时页码不越界。 */
const paged = computed(() => shown.value.slice((page.value - 1) * pageSize, page.value * pageSize));
watch([piFilter, usedFilter], () => (page.value = 1));
watch(() => shown.value.length, (n) => (page.value = Math.min(page.value, Math.max(1, Math.ceil(n / pageSize)))));
/** 生产组织：前缀下各规则的所属组织，没有的为「全局」。 */
const orgText = (g: any) => g.factories.map((f: string | null) => (f ? factoryName(f) : t("rules.ownerGlobal"))).join("、");
/** 自增前缀：「自增 0~9 · 客户 0832」（范围按位数补 0） */
/** 识别码号段状态（GET /rules/prefixes 的 inc.values，按 PI 与号段前缀对上） */
const incStatus = (row: any): string =>
  cur.value?.inc?.values?.find((v: any) => v.pi_no === row.pi_no && v.prefix === row.seq_prefix)?.status ?? "";
/** 识别码适用客户弹窗 */
const cust = reactive<{ open: boolean; list: string[]; saving: boolean; searching: boolean; found: string[] }>({
  open: false, list: [], saving: false, searching: false, found: [],
});
const custOptions = computed(() => Array.from(new Set([...cust.list, ...cust.found])));
async function searchCust(q: string) {
  cust.searching = true;
  try {
    cust.found = (await api.orderCustomers(q || undefined)) as string[];
  } finally {
    cust.searching = false;
  }
}
function openCust() {
  cust.list = [...(cur.value?.inc?.customers ?? [])];
  cust.found = [];
  cust.open = true;
  searchCust("");
}
/** 受影响的 PI（删除客户时）逐行写出：PI、客户、处理 */
const impactText = (xs: any[]) =>
  xs.map((x) => `${x.pi_no}（${x.customer}）：${x.actions.map((a: string) => t(`rules.incImpact.${a}`)).join("、")}`).join("\n");
async function saveCust() {
  const id = cur.value?.inc?.rule_id;
  if (!id) return;
  cust.saving = true;
  try {
    const r: any = await api.incCustomersImpact(id, { customers: cust.list });
    if (r?.impact?.length) {
      try {
        await ElMessageBox.confirm(impactText(r.impact), t("rules.incImpactTitle"), { type: "warning", customStyle: { whiteSpace: "pre-line" } });
      } catch {
        return;
      }
    }
    await api.setIncCustomers(id, { customers: cust.list });
    ElMessage.success(t("common.saved"));
    cust.open = false;
    await load();
  } finally {
    cust.saving = false;
  }
}
const incTagText = (inc: any) => {
  const pad = (v: number) => String(v).padStart(Number(inc.width) || 1, "0");
  return t("rules.incTag", { start: pad(inc.start), end: pad(inc.end), customer: (inc.customers ?? []).join("、") || "—" });
};
/** 右侧所选前缀：按前缀记住，重新加载后仍选中；不在当前筛选结果里时改选第一项。 */
const curPrefix = ref("");
const cur = computed(() => shown.value.find((g) => g.prefix === curPrefix.value) ?? null);
const pick = (g: any) => (curPrefix.value = g.prefix);
watch(paged, (list) => {
  if (!cur.value && list.length) curPrefix.value = list[0].prefix;
});
/** 编辑已用于生成的规则：前缀、进制、流水位数、字符表锁定（一个前缀一条规则，后端同样校验） */
const formatLocked = computed(() => !!editing.value?.rule_used);
/** 编辑时提示：格式不变 / 就地修改（规则没生成过号）/ 另出一版（生成过号）。 */
const editHint = computed(() => {
  const c = editing.value?.current;
  if (!c) return "";
  const charset = form.charset || CHARSETS[form.base];
  // 不带米数时补 0 位数、分隔符不起作用（后端统一存空）
  const len = form.with_length;
  const changed = c.prefix !== form.prefix || c.suffix !== form.suffix || c.base !== form.base || c.seq_len !== form.seq_len
    || c.charset !== charset || !!c.with_length !== len || !!c.with_unit !== form.with_unit
    || (c.length_pad || 0) !== (len ? form.length_pad || 0 : 0)
    || (c.length_sep_before || "") !== (len ? form.sep_before : "") || (c.length_sep_after || "") !== (len ? form.sep_after : "")
    || (c.attach_width || 0) !== (form.attach_width || 0) || (c.attach_tail || "") !== (form.attach_tail || "");
  if (!changed) return "";
  // 当前版本已用于生成或已归属 PI：另出一版；否则（含规则库改版后还没人用的当前版本）就地修改
  return c.used || c.claimed_pi ? "NEW_VERSION" : "IN_PLACE";
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
    prefix: c.prefix, suffix: c.suffix, base: c.base, seq_len: c.seq_len, with_length: !!c.with_length,
    length_pad: c.length_pad || 0, sep_before: c.length_sep_before || "", sep_after: c.length_sep_after || "", with_unit: !!c.with_unit,
    charset: c.charset === CHARSETS[c.base] ? "" : c.charset, attach_width: c.attach_width || 0, attach_tail: c.attach_tail || "",
  });
  dialog.value = true;
}

/** 版本的编码参数（与编辑表单提交的一致），用于取示例号。 */
const specOf = (c: any) => ({
  prefix: c.prefix, suffix: c.suffix, base: c.base, seq_len: c.seq_len, charset: c.charset, with_length: !!c.with_length,
  length_pad: c.length_pad || 0, sep_before: c.length_sep_before || "", sep_after: c.length_sep_after || "", with_unit: !!c.with_unit,
  attach_width: c.attach_width || 0, attach_tail: c.attach_tail || "",
});

/** 前缀详情：各 PI 占的流水、各规则的开始 SN / 当前最大 SN（GET /rules/prefixes/usage）与所选规则的示例号（前 3 个）。 */
const detail = reactive({
  ruleId: 0, loading: false, items: [] as any[], error: "", parts: [] as any[],
  usage: [] as any[], usageLoading: false, usageError: "", ruleSns: {} as Record<number, any>,
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
  Object.assign(detail, { ruleId: cur.value?.rules[0]?.rule.id ?? 0, usage: [], usageError: "", ruleSns: {} });
  loadSample();
  if (!prefix) return;
  detail.usageLoading = true;
  try {
    const r: any = await api.rulePrefixUsage(prefix);
    if (curPrefix.value === prefix) {
      detail.usage = r.pis;
      detail.ruleSns = Object.fromEntries((r.rule_sns ?? []).map((x: any) => [x.rule_id, x]));
    }
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
  // 附件项次（前缀与流水之间，定长：位数 + 固定字符）
  const attLen = c.attach_width ? c.attach_width + (c.attach_tail || "").length : 0;
  const out = [{ k: "prefix", label: t("rules.prefix"), text: c.prefix }];
  if (attLen) out.push({ k: "attach", label: t("generate.rule.part.ATTACH"), text: s.sn.slice(c.prefix.length, c.prefix.length + attLen) });
  out.push({ k: "seq", label: t("rules.seqNo"), text: s.seq_text });
  let mid = s.sn.slice(c.prefix.length + attLen + s.seq_text.length, s.sn.length - (c.suffix || "").length);
  if (c.with_unit && unit && mid.endsWith(unit)) mid = mid.slice(0, mid.length - unit.length);
  else unit = "";
  if (c.with_length && mid) out.push({ k: "length", label: t("common.length"), text: mid });
  if (unit) out.push({ k: "unit", label: t("common.unit"), text: unit });
  if (c.suffix) out.push({ k: "suffix", label: t("rules.suffixCol"), text: c.suffix });
  return out;
}

/** 示例号：所选规则在这个前缀上的版本（前 3 个，米数、产品单位用示例值）。 */
async function loadSample() {
  const x = detailRule.value;
  Object.assign(detail, { items: [], error: "", parts: [] });
  const c = x?.version;
  if (!c) return;
  detail.loading = true;
  try {
    const r: any = await api.previewRule(specOf(c));
    if (detailRule.value !== x) return; // 等结果时已改看别的规则
    // 只列前 3 个示例号
    detail.items = r.samples.slice(0, 3).map((s: any) => ({
      ...s, prefix: c.prefix, suffix: c.suffix, base: c.base, seq_len: c.seq_len,
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
  attach_width: form.attach_width || 0, attach_tail: form.attach_tail || "",
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
      const r: any = await api.updateRule(editing.value.rule.id, spec());
      ElMessage.success(t(`rules.result${r.action}`, { v: r.rule?.current?.display_version ?? r.rule?.rule?.current_version }));
    } else {
      await api.createRule({ rule_code: form.rule_code, factory_code: form.factory_code || null, ...spec() });
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
  await ElMessageBox.confirm(t("rules.deleteConfirm", { code: r.rule_code }), t("common.delete"), {
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
.hint { width: 100%; line-height: 1.5; margin-top: 2px; }
.full { width: 100%; }
.w160 { width: 160px; }
.samples { display: flex; flex-wrap: wrap; gap: 6px 8px; }
.max { color: var(--el-color-warning); }
.err { color: var(--el-color-danger); font-size: 12px; }
/* 左右分栏：左侧前缀清单固定宽度，右侧详情自适应；窄屏上下排列 */
.split { display: flex; flex-direction: row !important; gap: 16px; align-items: stretch; }
.side { width: 360px; flex: none; display: flex; flex-direction: column; gap: 10px; padding: 14px; min-height: 0; }
.side-filter { display: flex; flex-direction: column; gap: 8px; }
.pi-sel { width: 100%; }
.side-filter :deep(.el-segmented) { width: 100%; }
.plist { flex: 1; min-height: 120px; overflow-y: auto; display: flex; flex-direction: column; border: 1px solid var(--app-line); border-radius: 8px; }
.pitem { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 10px 12px; cursor: pointer; border-bottom: 1px solid var(--app-line); outline: none; transition: background .15s; }
.pitem:last-of-type { border-bottom: none; }
.pitem:hover, .pitem:focus-visible { background: var(--el-fill-color-light); }
.pitem.on { background: var(--el-color-primary-light-9); box-shadow: inset 3px 0 0 var(--el-color-primary); }
.pfx { flex: none; max-width: 40%; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pitem .meta { flex: 1; min-width: 0; font-size: 12px; color: var(--app-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pitem .slot { flex: none; font-size: 12px; color: var(--el-color-success); }
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
.part.attach { background: var(--el-color-warning-light-9); color: var(--el-color-warning-dark-2); }
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
