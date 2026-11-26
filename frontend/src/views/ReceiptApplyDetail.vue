<template>
  <div class="modal-backdrop" data-testid="continue-modal" @click.self="emit('close')">
    <div data-testid="detail-modal">
      <div
        class="modal detail-modal"
        role="dialog"
        aria-modal="true"
        aria-label="申请详情继续办理"
        data-testid="apply-detail"
      >
        <div data-testid="receipt-detail">
          <header class="detail-head">
            <div>
              <h3 data-testid="detail-title">申请详情 · 继续办理</h3>
              <p class="detail-sub" data-testid="detail-subtitle">
                原签收与重发资格均由服务端核对，处理结果开具可追查处置回执
              </p>
            </div>
            <button class="btn ghost" type="button" data-testid="close-detail" @click="emit('close')">
              返回列表
            </button>
          </header>

          <p v-if="loading" class="detail-loading">服务端正在核对原签收、重发资格与实际送达…</p>
          <p v-else-if="errorMessage" class="error-text" data-testid="detail-error">{{ errorMessage }}</p>

          <div
            v-else-if="detail && detail.ok === false"
            class="detail-section"
            data-testid="detail-no-apply"
          >
            <p class="muted">{{ detail.message || '该回执还没有重发 / 补签申请。' }}</p>
            <button class="btn primary" type="button" data-testid="create-apply" :disabled="busy" @click="createApply">
              发起重发申请并继续办理
            </button>
          </div>

          <template v-else-if="detail && detail.ok !== false">
            <!-- 办理反馈 -->
            <p
              v-if="notice"
              class="detail-notice"
              :class="{ 'is-error': noticeError }"
              role="status"
              data-testid="detail-message"
            >
              {{ notice }}
            </p>

            <!-- 申请概要 -->
            <section class="detail-section" data-testid="apply-summary">
              <h4>
                {{ app?.申请标题 || '重发 / 补签申请' }}
                <span class="tag" data-testid="apply-status">{{ app?.status }}</span>
              </h4>
              <dl class="kv-grid">
                <div><dt>申请编号</dt><dd data-testid="apply-no">{{ app?.申请编号 || '—' }}</dd></div>
                <div><dt>申请类型</dt><dd>{{ app?.申请类型 || '—' }}</dd></div>
                <div><dt>分发编号</dt><dd data-testid="apply-batch">{{ batch }}</dd></div>
                <div><dt>订阅方</dt><dd>{{ app?.订阅方 || detail.receipt?.订阅方 || '—' }}</dd></div>
                <div>
                  <dt>是否实际发出</dt>
                  <dd data-testid="apply-sent">{{ app?.sent ? '已实际发出' : '还未实际发出（发起重发请求不算收到回执）' }}</dd>
                </div>
                <div><dt>当前断点</dt><dd>{{ app?.断点 || '—' }}</dd></div>
              </dl>
            </section>

            <!-- 服务端核对：第一次签收 / 原签收 -->
            <section class="detail-section" data-testid="original-signoff">
              <h4>第一次签收（原签收）</h4>
              <div data-testid="first-signoff">
                <template v-if="first">
                  <dl class="kv-grid">
                    <div><dt>签收编号</dt><dd>{{ first.签收编号 }}</dd></div>
                    <div><dt>签收结果</dt><dd data-testid="first-signoff-result">{{ first.签收结果 }}</dd></div>
                    <div><dt>是第几次</dt><dd>第 {{ first.第几次 || 1 }} 次</dd></div>
                    <div><dt>签收来源</dt><dd>{{ first.来源 || '订阅方' }}</dd></div>
                    <div><dt>签收范围</dt><dd>{{ first.签收范围 || '整批' }}</dd></div>
                    <div><dt>记录数 / 破损</dt><dd>{{ first.记录数 ?? '—' }} / {{ first.破损数 || 0 }}</dd></div>
                  </dl>
                  <p class="ok-text">该签收为服务端核对出的有效原签收；重复补投不会让旧行与新回执同时生效。</p>
                </template>
                <p v-else class="muted" data-testid="no-signoff-yet">
                  服务端未核对到任何签收记录：该分发没有签收（区别于“已经拒收”）。
                </p>
              </div>
              <p class="inline-count" data-testid="signoff-count">
                该分发有效签收次数：<strong>{{ effectiveSignoffCount }}</strong>
                （签收记录总数 {{ signoffRows.length }}）
              </p>
            </section>

            <!-- 补签 / 重发申请各自结果 + 重发资格 -->
            <section class="detail-section" data-testid="resend-eligibility">
              <h4>补签申请结果与重发资格（服务端核对）</h4>
              <div data-testid="apply-result" class="apply-result">
                {{ detail.补签申请结果?.结果 || '—' }}
              </div>
              <div class="eligibility" :data-eligible="String(!!eligibility?.eligible)" data-testid="eligibility-box">
                <span class="tag" :class="eligibility?.eligible ? 'tag-ok' : 'tag-stop'" data-testid="eligibility-code">
                  {{ eligibility?.code || detail.核对?.code || 'UNKNOWN' }}
                </span>
                <strong data-testid="eligibility-verdict">
                  {{ eligibility?.eligible
                    ? '仍需要送给订阅方（具备重发资格）'
                    : eligibility?.eligible === 'replacement-only'
                      ? '整批无需重发，仅可补寄核准条目'
                      : '不需要再送给订阅方（不具备重发资格）' }}
                </strong>
                <p class="eligibility-reason" data-testid="eligibility-reason">
                  {{ eligibility?.reason || detail.核对?.原因 || '—' }}
                </p>
              </div>
            </section>

            <!-- 两种不同原因的对照（无签收 / 已拒收），便于值班员区分 -->
            <section v-if="reasonCompare" class="detail-section reason-compare" data-testid="reason-compare">
              <h4>无签收 与 已拒收 的不同原因</h4>
              <p><span class="tag">没有签收</span> {{ reasonCompare.no }}</p>
              <p><span class="tag tag-stop">已经拒收</span> {{ reasonCompare.rejected }}</p>
            </section>

            <!-- 8 条破损补寄：异常编号 / 按原单移交 -->
            <section v-if="isReplacement" class="detail-section" data-testid="replacement-box">
              <h4>破损条目补寄（仅实际获准条目可交分发任务）</h4>
              <p class="approved-line" data-testid="approved-items">
                已核准 <strong data-testid="approved-count">{{ app?.获准数量 || 0 }}</strong> 条：
                {{ approvedText }}
              </p>
              <div class="exception-row" data-testid="exception-no-input">
                <label for="exception-no">异常编号</label>
                <input
                  id="exception-no"
                  v-model="exceptionNo"
                  type="text"
                  name="exception-no"
                  aria-label="异常编号"
                  placeholder="请输入值班员核准取得的异常编号，如 YC-20261124-02"
                  data-testid="exception-no"
                />
                <button
                  class="btn primary"
                  type="button"
                  data-testid="submit-exception"
                  :disabled="busy"
                  @click="submitException"
                >
                  提交异常编号
                </button>
              </div>
              <div class="handoff-row">
                <button
                  class="btn"
                  type="button"
                  data-testid="handoff-dist-task"
                  :disabled="busy"
                  @click="handoff"
                >
                  按原单把补寄交给分发任务接手
                </button>
                <button
                  v-if="replacementTask"
                  class="btn ghost"
                  type="button"
                  data-testid="goto-dist-task"
                  @click="goDistTask"
                >
                  前往分发任务领取（{{ replacementTask.分发批次 }}）
                </button>
              </div>
              <p v-if="replacementTask" class="ok-text" data-testid="handoff-task-info">
                分发任务 {{ replacementTask.分发批次 }} 已建立，可领取数量
                {{ replacementTask.获准数量 }}，任务 id {{ replacementTask.id }}
              </p>
            </section>

            <!-- 仍具备资格：中断后继续发出 -->
            <section v-if="eligibility?.eligible === true" class="detail-section">
              <button class="btn primary" type="button" data-testid="resume-resend" :disabled="busy" @click="resume">
                继续未完成的重发 / 实际发出
              </button>
            </section>

            <!-- 终止：原因附到同一分发编号并开处置回执 -->
            <section v-if="canTerminate" class="detail-section terminate-box" data-testid="terminate-box">
              <label for="terminate-reason">本次终止 / 补签的处理原因（附到同一分发编号）</label>
              <textarea
                id="terminate-reason"
                v-model="terminateReason"
                rows="2"
                aria-label="处理原因"
                placeholder="可补充处理原因；留空则使用服务端核对原因"
                data-testid="terminate-reason"
              />
              <button
                class="btn danger"
                type="button"
                data-testid="terminate-apply"
                :disabled="busy"
                @click="terminate"
              >
                终止申请（不再重发）
              </button>
            </section>

            <!-- 对账确认（幂等，同一分发同一结果只有一份） -->
            <section class="detail-section" data-testid="reconciliation-list">
              <h4>对账确认 <span class="inline-count" data-testid="reconciliation-count">共 {{ reconciliations.length }} 份</span></h4>
              <ul v-if="reconciliations.length" class="plain-list">
                <li v-for="r in reconciliations" :key="r.id" :data-testid="`recon-row-${r.id}`">
                  {{ r.确认编号 }} · {{ r.签收结果 }} · {{ r.确认结论 }}
                </li>
              </ul>
              <p v-else class="muted">尚无对账确认。</p>
            </section>

            <!-- 可追查处置回执 -->
            <section class="detail-section" data-testid="disposition-list">
              <h4>处置回执（可追查） <span class="inline-count" data-testid="disposition-count">共 {{ dispositions.length }} 份</span></h4>
              <ul v-if="dispositions.length" class="plain-list">
                <li v-for="d in dispositions" :key="d.id" :data-testid="`disposition-row-${d.id}`">
                  <strong>{{ d.处置回执编号 }}</strong> · {{ d.处置类型 }} · {{ d.处理原因 }}
                </li>
              </ul>
              <p v-else class="muted">继续办理后将开具处置回执。</p>
            </section>

            <!-- 已匹配实际送达（中断后仍可回读） -->
            <section class="detail-section" data-testid="matched-deliveries">
              <h4>已匹配的实际送达（可回读）</h4>
              <ul v-if="deliveries.length" class="plain-list">
                <li v-for="(d, i) in deliveries" :key="i" :data-testid="`delivery-row-${i}`">
                  {{ d.分发编号 }} · {{ d.签收渠道 || d.来源 || '实际送达' }} · {{ d.实际送达时间 || '时间未知' }}
                </li>
              </ul>
              <p v-else class="muted">暂未匹配到实际送达记录。</p>
            </section>

            <!-- 同一分发编号下的事件 / 原因轨迹 -->
            <section class="detail-section" data-testid="event-trail">
              <h4>同一分发编号的处理轨迹</h4>
              <ul v-if="events.length" class="timeline-list">
                <li v-for="e in events" :key="e.id">
                  <span class="muted">{{ e.时间?.slice(0, 19) }}</span>
                  【{{ e.事件类型 }}】{{ e.处理原因 }}
                </li>
              </ul>
            </section>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { continueReceipt, runAction } from '@/api/backend-service'

const props = defineProps<{ receiptId: number }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const router = useRouter()
const detail = ref<Record<string, any> | null>(null)
const loading = ref(true)
const busy = ref(false)
const errorMessage = ref('')
const notice = ref('')
const noticeError = ref(false)
const exceptionNo = ref('')
const terminateReason = ref('')

const app = computed<Record<string, any> | null>(() => detail.value?.application ?? null)
const eligibility = computed<Record<string, any> | null>(() => detail.value?.重发资格 ?? null)
const first = computed<Record<string, any> | null>(() => detail.value?.原签收 ?? null)
const signoffRows = computed<Record<string, any>[]>(() => detail.value?.签收记录 ?? [])
const reconciliations = computed<Record<string, any>[]>(() => detail.value?.对账确认 ?? [])
const dispositions = computed<Record<string, any>[]>(() => detail.value?.处置回执 ?? [])
const deliveries = computed<Record<string, any>[]>(() => detail.value?.实际送达 ?? [])
const events = computed<Record<string, any>[]>(() => detail.value?.事件轨迹 ?? [])
const batch = computed(() => app.value?.分发编号 ?? detail.value?.receipt?.分发批次 ?? '')
const replacementTask = computed<Record<string, any> | null>(
  () => detail.value?.补寄分发任务 ?? detail.value?.replacement_task ?? null,
)
const isReplacement = computed(
  () => app.value?.申请类型 === '补寄' || Number(app.value?.获准数量 || 0) > 0,
)
const approvedText = computed(() => {
  const items = app.value?.获准条目列表
  if (Array.isArray(items) && items.length) return items.join('、')
  return app.value?.获准条目 || '—'
})
const effectiveSignoffCount = computed(
  () => signoffRows.value.filter((s) => s.effective && s.归一结果 === '签收成功').length || 1,
)
const canTerminate = computed(() => {
  const status = String(app.value?.status || '')
  return !['已终止', '无需重发', '已完成'].includes(status)
})
const reasonCompare = computed(() => {
  // 仅在当前单本身是“无签收”或“拒收”时，向服务端核对另一类原因做对照
  const code = eligibility.value?.code
  if (code === 'NO_SIGNOFF') {
    return { no: eligibility.value?.reason, rejected: REJECTED_HINT }
  }
  if (code === 'REJECTED') {
    return { no: NOSIGN_HINT, rejected: eligibility.value?.reason }
  }
  return null
})

const NOSIGN_HINT = '这是没有签收的分发（订阅方尚未签收 / 未回传回执），可继续重发。'
const REJECTED_HINT = '这是已经拒收的分发，需按拒收异常核准，不能按“没有签收”处理。'

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    detail.value = await continueReceipt(props.receiptId)
    exceptionNo.value = String(detail.value?.application?.异常编号 ?? '')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '申请详情读取失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.receiptId, reload, { immediate: true })

async function act(action: string, params: Record<string, unknown> = {}, okHint = '') {
  busy.value = true
  notice.value = ''
  noticeError.value = false
  try {
    const result = await runAction('dist_receipt', props.receiptId, action, params)
    notice.value = okHint || result.message || (result.ok ? '办理完成' : '办理未完成')
    noticeError.value = !result.ok
    await reload()
    emit('changed')
    return result
  } catch (error) {
    noticeError.value = true
    notice.value = error instanceof Error ? error.message : '操作失败'
    return null
  } finally {
    busy.value = false
  }
}

function submitException() {
  if (!exceptionNo.value.trim()) {
    noticeError.value = true
    notice.value = '请先填写异常编号'
    return
  }
  return act('提交异常编号', { 异常编号: exceptionNo.value.trim() })
}

function createApply() {
  return act('重发回执请求')
}

function handoff() {
  return act('按原单补寄', exceptionNo.value.trim() ? { 异常编号: exceptionNo.value.trim() } : {})
}

function terminate() {
  return act('终止申请', terminateReason.value.trim() ? { reason: terminateReason.value.trim() } : {})
}

function resume() {
  return act('继续发出')
}

function goDistTask() {
  // 直接切到分发任务模块；回执工作台卸载后详情状态自然销毁
  void router.push('/dist_task')
}
</script>

<style scoped>
.detail-modal { width: min(860px, 100%); }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.detail-sub { color: var(--muted); font-size: 12px; margin: 4px 0 0; }
.detail-loading { padding: 24px 0; color: var(--muted); }
.detail-section { border-top: 1px solid var(--border); padding: 12px 0; }
.detail-section h4 { margin: 0 0 8px; font-size: 14px; display: flex; align-items: center; gap: 8px; }
.kv-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 16px; margin: 0; }
.kv-grid dt { font-size: 12px; color: var(--muted); }
.kv-grid dd { margin: 0 0 4px; font-size: 13px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 999px; background: #eef2ff; color: #3730a3; font-size: 12px; }
.tag-ok { background: #ecfdf3; color: #027a48; }
.tag-stop { background: #fef3f2; color: #b42318; }
.ok-text { color: #027a48; font-size: 13px; margin: 6px 0 0; }
.muted { color: var(--muted); font-size: 13px; }
.inline-count { font-size: 12px; color: var(--muted); margin: 6px 0 0; }
.apply-result { font-size: 13px; background: #f8fafc; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; }
.eligibility { border: 1px solid var(--border); border-radius: 8px; padding: 10px; display: grid; gap: 6px; }
.eligibility-reason { margin: 0; font-size: 13px; line-height: 1.6; }
.reason-compare p { font-size: 13px; display: flex; gap: 8px; align-items: flex-start; }
.approved-line { font-size: 13px; }
.exception-row { display: flex; gap: 8px; align-items: center; margin: 10px 0; flex-wrap: wrap; }
.exception-row label { font-size: 13px; }
.exception-row input { flex: 1; min-width: 240px; padding: 8px; border: 1px solid var(--border); border-radius: 6px; }
.handoff-row { display: flex; gap: 8px; flex-wrap: wrap; }
.terminate-box { display: grid; gap: 8px; }
.terminate-box textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-family: inherit; }
.btn.danger { background: #d92d20; border-color: #d92d20; color: #fff; }
.plain-list { margin: 0; padding-left: 18px; display: grid; gap: 4px; font-size: 13px; }
.timeline-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12.5px; }
.detail-notice { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
.detail-notice.is-error { background: #fef3f2; border-color: #fda29b; color: #b42318; }
</style>
