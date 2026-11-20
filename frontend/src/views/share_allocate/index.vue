<template>
  <section class="page" data-module="share_allocate">
    <header class="page-head">
      <div>
        <h2>共享源采集分摊</h2>
        <p class="page-desc">
          根据来源样本与归属时段计算可共享指标，生成共享源采集分摊表：列明各团队权限内的有效分钟数与采集量；
          两队都可查看的来源只计为一个物理来源，交集窗口保留记录标识，整体采集量始终按唯一原始样本重算。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="runRecalc">重新分摊</button>
        <button class="btn" type="button" @click="exportTasks">导出任务清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>分摊口径</span>
        <select v-model="caliber">
          <option value="full">考核口径（全额各计）</option>
          <option value="split">分摊口径（按有效分钟拆分）</option>
          <option value="primary">主责口径（重叠归主责团队）</option>
        </select>
      </label>
      <label class="filter-item">
        <span>团队</span>
        <select v-model="teamId">
          <option value="">全部团队</option>
          <option v-for="team in teams" :key="String(team['团队编号'])" :value="team['团队编号']">
            {{ team['团队名称'] }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>周期开始</span>
        <input v-model="start" placeholder="2026-11-20T20:00:00" />
      </label>
      <label class="filter-item">
        <span>周期结束</span>
        <input v-model="end" placeholder="2026-11-20T21:30:00" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-item"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 共享源采集分摊表 -->
    <table v-if="activeTab === 'allocation'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in allocationColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in allocationRows" :key="String(row['分摊行号'])" :class="{ 'row-shared': row['是否交集'] }">
          <td v-for="column in allocationColumns" :key="String(column)">{{ formatCell(row[column]) }}</td>
        </tr>
        <tr v-if="!allocationRows.length">
          <td :colspan="allocationColumns.length" class="empty-state">当前条件下没有可分摊的观测窗口</td>
        </tr>
      </tbody>
    </table>

    <!-- 分部门来源明细（采集报告口径） -->
    <table v-else-if="activeTab === 'department'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in departmentColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in departmentRows" :key="`${row['团队编号']}-${row['来源编号']}`">
          <td v-for="column in departmentColumns" :key="column">{{ formatCell(row[column]) }}</td>
        </tr>
        <tr v-if="!departmentRows.length">
          <td :colspan="departmentColumns.length" class="empty-state">暂无分部门来源明细</td>
        </tr>
      </tbody>
    </table>

    <!-- 整体对账行 -->
    <table v-else-if="activeTab === 'reconciliation'" class="data-table">
      <thead>
        <tr><th>对账指标</th><th>数值</th><th>说明</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in reconciliationRows" :key="item.label">
          <td>{{ item.label }}</td>
          <td>{{ formatCell(item.value) }}</td>
          <td>{{ item.hint }}</td>
        </tr>
      </tbody>
    </table>

    <!-- 基础数据 -->
    <div v-else class="base-grid">
      <article v-for="base in baseTables" :key="base.key" class="base-card">
        <h3>{{ base.title }}</h3>
        <table class="data-table compact">
          <thead>
            <tr><th v-for="column in base.columns" :key="column">{{ column }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in baseRows[base.key]" :key="index">
              <td v-for="column in base.columns" :key="column">{{ formatCell(row[column]) }}</td>
            </tr>
          </tbody>
        </table>
      </article>
    </div>

    <footer class="page-foot">
      <span>分摊行 {{ allocationRows.length }} 条 · 部门明细 {{ departmentRows.length }} 条</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/share_allocate'

const tabs = [
  { key: 'allocation', label: '共享源采集分摊表' },
  { key: 'department', label: '分部门来源明细' },
  { key: 'reconciliation', label: '整体对账行' },
  { key: 'base', label: '来源/归属/样本' },
] as const

const allocationColumns = [
  '分摊行号', '团队名称', '来源编号', '来源名称', '物理来源标识',
  '观测窗口开始', '观测窗口结束', '有效分钟数', '物理采集量', '分摊采集量',
  '是否交集', '交集记录标识', '共享团队', '是否主责', '原始样本编号', '口径说明',
]
const departmentColumns = [
  '团队名称', '来源编号', '来源名称', '物理来源标识', '有效分钟数',
  '物理采集量', '分摊采集量', '交集窗口数', '交集记录标识', '原始样本数', '原始样本编号', '口径说明',
]

const baseTables = [
  { key: 'sources', title: '来源登记（物理来源唯一）', columns: ['来源编号', '来源名称', '物理来源标识', '登记团队', '采样周期分钟'] },
  { key: 'teams', title: '团队与考核报表', columns: ['团队编号', '团队名称', '考核报表'] },
  { key: 'attributions', title: '归属时段（允许同时覆盖）', columns: ['归属编号', '来源编号', '团队编号', '开始时间', '结束时间', '主责团队'] },
  { key: 'samples', title: '唯一原始样本', columns: ['样本编号', '来源编号', '观测开始', '观测结束', '采集量'] },
] as const

const activeTab = ref<string>('allocation')
const caliber = ref('full')
const teamId = ref('')
const start = ref('2026-11-20T20:00:00')
const end = ref('2026-11-20T21:30:00')
const errorMessage = ref('')

const allocationRows = ref<Row[]>([])
const departmentRows = ref<Row[]>([])
const reconciliation = ref<Record<string, string | number | boolean | null>>({})
const teams = ref<Row[]>([])
const baseRows = ref<Record<string, Row[]>>({ sources: [], teams: [], attributions: [], samples: [] })

const stats = computed(() => [
  { label: '物理来源数', value: Number(reconciliation.value['物理来源数'] ?? 0) },
  { label: '共享窗口数', value: Number(reconciliation.value['共享窗口数'] ?? 0) },
  { label: '共享覆盖分钟', value: Number(reconciliation.value['共享覆盖分钟数'] ?? 0) },
  { label: '去重前/后采集量', value: `${Number(reconciliation.value['去重前采集量'] ?? 0)} / ${Number(reconciliation.value['全局去重采集量'] ?? 0)}` },
  { label: '按样本重算', value: Number(reconciliation.value['按样本重算采集量'] ?? 0) },
])

const reconciliationRows = computed(() => [
  { label: '分摊口径', value: reconciliation.value['口径说明'], hint: '口径只决定分摊采集量，不改变原始样本事实' },
  { label: '物理来源数', value: reconciliation.value['物理来源数'], hint: '两队都可查看的来源只计一个物理来源' },
  { label: '归属记录数', value: reconciliation.value['归属记录数'], hint: '同一来源可同时归属多个团队' },
  { label: '归属与物理来源差值', value: reconciliation.value['归属与物理来源差值'], hint: '重复归属不等于重复采集' },
  { label: '物理观测窗口数', value: reconciliation.value['物理观测窗口数'], hint: '同一来源同一十分钟窗口只算一份物理事实' },
  { label: '共享窗口数', value: reconciliation.value['共享窗口数'], hint: '被两个及以上团队同时持有的窗口' },
  { label: '共享覆盖分钟数', value: reconciliation.value['共享覆盖分钟数'], hint: '交集窗口的物理时长，不按团队倍数累加' },
  { label: '重叠归属分钟数', value: reconciliation.value['重叠归属分钟数'], hint: '各团队在交集窗口内的有效分钟之和' },
  { label: '去重前采集量', value: reconciliation.value['去重前采集量'], hint: '分部门明细直接相加（含重复归属）' },
  { label: '全局去重采集量', value: reconciliation.value['全局去重采集量'], hint: '按物理窗口去重后的实际采集' },
  { label: '去重前后差值', value: reconciliation.value['去重前后差值'], hint: '与共享覆盖的重复归属一一对应' },
  { label: '分部门分摊合计', value: reconciliation.value['分部门分摊合计'], hint: 'split/primary 口径下应等于全局去重采集量' },
  { label: '唯一原始样本数', value: reconciliation.value['唯一原始样本数'], hint: '整体采集量重算的唯一事实来源' },
  { label: '按样本重算采集量', value: reconciliation.value['按样本重算采集量'], hint: reconciliation.value['重算一致'] ? '与全局去重采集量一致' : '与全局去重采集量不一致，需排查' },
  { label: '分部门合计与去重一致', value: reconciliation.value['分部门合计与去重一致'] === null ? '—（考核口径允许重复计）' : formatCell(reconciliation.value['分部门合计与去重一致']), hint: '考核口径各自全额计，对账以去重行与重算行为准' },
])

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (value === true) return '是'
  if (value === false) return '否'
  return String(value)
}

function buildQuery(extra: Record<string, string> = {}): string {
  const params = new URLSearchParams()
  params.set('caliber', caliber.value)
  if (teamId.value) params.set('team_id', teamId.value)
  if (start.value) params.set('start', start.value)
  if (end.value) params.set('end', end.value)
  Object.entries(extra).forEach(([key, value]) => params.set(key, value))
  return params.toString()
}

function resetFilters() {
  caliber.value = 'full'
  teamId.value = ''
  start.value = '2026-11-20T20:00:00'
  end.value = '2026-11-20T21:30:00'
  void reload()
}

function exportTasks() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runRecalc() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/1/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '重新分摊', caliber: caliber.value } }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '重新分摊失败')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新分摊失败'
  }
}

async function loadBaseTables() {
  const keys = ['sources', 'teams', 'attributions', 'samples'] as const
  const results = await Promise.all(
    keys.map(async (key) => {
      const response = await request(`${ENDPOINT}/${key}`)
      if (!response.ok) throw new Error(`基础数据 ${key} 读取失败`)
      const payload = await response.json()
      return [key, payload.items ?? []] as const
    }),
  )
  baseRows.value = Object.fromEntries(results)
  teams.value = baseRows.value.teams
}

async function reload() {
  errorMessage.value = ''
  try {
    const query = buildQuery()
    const [allocationResp, departmentResp, reconResp] = await Promise.all([
      request(`${ENDPOINT}/allocation?${query}`),
      request(`${ENDPOINT}/department_details?${query}`),
      request(`${ENDPOINT}/reconciliation?${buildQuery({})}`),
    ])
    if (!allocationResp.ok || !departmentResp.ok || !reconResp.ok) {
      throw new Error('共享源分摊数据读取失败')
    }
    const [allocationPayload, departmentPayload, reconPayload] = await Promise.all([
      allocationResp.json(),
      departmentResp.json(),
      reconResp.json(),
    ])
    allocationRows.value = allocationPayload.items ?? []
    departmentRows.value = departmentPayload.items ?? []
    reconciliation.value = reconPayload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '共享源分摊数据读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadBaseTables()])
})
</script>

<style scoped>
.tab-bar {
  display: flex;
  gap: 8px;
  margin: 12px 0;
  border-bottom: 1px solid var(--border-color, #e3e6ee);
}
.tab-item {
  padding: 8px 16px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #5b6270;
  border-bottom: 2px solid transparent;
}
.tab-item.active {
  color: #2456d6;
  border-bottom-color: #2456d6;
  font-weight: 600;
}
.row-shared {
  background: rgba(36, 86, 214, 0.08);
}
.base-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
}
.base-card {
  border: 1px solid var(--border-color, #e3e6ee);
  border-radius: 8px;
  padding: 12px;
}
.base-card h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.data-table.compact {
  font-size: 12px;
}
</style>
