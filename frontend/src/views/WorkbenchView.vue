<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <template v-if="meta.key === 'dist_receipt'">
              <span :data-testid="`detail-${row.id}`" class="inline-hook">
                <span :data-testid="`continue-${row.id}`" class="inline-hook">
                  <button
                    class="link continue-link"
                    type="button"
                    :data-testid="`action-${row.id}-继续办理`"
                    @click="openContinue(row)"
                  >
                    继续办理
                  </button>
                </span>
              </span>
            </template>
            <template v-if="meta.key === 'dist_task'">
              <button
                class="link"
                type="button"
                :data-testid="`action-${row.id}-核对实际送达`"
                @click="taskRowAction('核对实际送达', row)"
              >
                核对实际送达
              </button>
              <span v-if="canClaim(row)" :data-testid="`claim-${row.id}`" class="inline-hook">
                <button
                  class="link claim-link"
                  type="button"
                  :data-testid="`action-${row.id}-领取`"
                  @click="taskRowAction('领取', row)"
                >
                  领取
                </button>
              </span>
              <button
                v-if="canDeliver(row)"
                class="link"
                type="button"
                :data-testid="`action-${row.id}-确认送达`"
                @click="taskRowAction('确认送达', row)"
              >
                确认送达
              </button>
            </template>
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <ReceiptApplyDetail
      v-if="meta.key === 'dist_receipt' && continueId !== null"
      :receipt-id="continueId"
      @close="closeContinue"
      @changed="reload"
    />

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="noticeMessage" class="ok-text" data-testid="action-notice">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

import ReceiptApplyDetail from './ReceiptApplyDetail.vue'

const props = defineProps<{
  moduleKey: string
  entryId?: string
  applyId?: string
}>()
const meta = moduleMeta(props.moduleKey)
const route = useRoute()
const router = useRouter()
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const continueId = ref<number | null>(null)
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message || ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

function canClaim(row: EntryRow): boolean {
  if (row.claimed === true) return false
  return Boolean(row.补寄 || Number(row.获准数量 || 0) > 0 || row.获准条目)
}

function canDeliver(row: EntryRow): boolean {
  if (row.status === '已送达') return false
  // 按题面顺序：值班员先「领取实际获准的两条」，随后才确认补寄送达
  return row.claimed === true || row.status === '分发中'
}

async function taskRowAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await applyAction('dist_task', Number(row.id), action)
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    noticeMessage.value = result.message || ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

function resolveInitialContinueId(): number | null {
  if (props.moduleKey !== 'dist_receipt') return null
  const fromPath = Number(props.entryId ?? props.applyId ?? '')
  if (Number.isInteger(fromPath) && fromPath > 0) return fromPath
  const fromQuery = Number(route.query.apply ?? '')
  if (Number.isInteger(fromQuery) && fromQuery > 0) return fromQuery
  return null
}

function openContinue(row: EntryRow) {
  continueId.value = Number(row.id)
  errorMessage.value = ''
  void router.replace({ query: { ...route.query, apply: String(row.id) } })
}

function closeContinue() {
  continueId.value = null
  // 若是从 /continue/:entryId 深链进入，关闭时回到干净的模块列表路径，
  // 避免路由参数被 watcher 重新解析而再次打开详情。
  if (props.entryId || props.applyId || route.query.apply) {
    void router.replace({ path: `/${props.moduleKey}` })
  }
}

watch(
  () => [props.entryId, props.applyId, route.query.apply] as const,
  () => {
    const next = resolveInitialContinueId()
    if (next !== null) continueId.value = next
  },
)

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(async () => {
  continueId.value = resolveInitialContinueId()
  await reload()
})
</script>
