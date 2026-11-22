<template>
  <section class="page" data-module="archive_merge">
    <header class="page-head">
      <div>
        <h2>归档下架与归并取数</h2>
        <p class="page-desc">
          过期导出文件按归档下架清册执行下架，同步处理文件状态与取数资格并关联归并；
          数据归并按清册辨认当前输入资格，受阻任务可补来源恢复。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新</button>
        <button class="btn primary" type="button" @click="executeRoster">执行全部待下架</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">清册文件总数</span>
        <strong class="stat-value">{{ roster.total ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待下架（已到期）</span>
        <strong class="stat-value">{{ roster.due_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已下架</span>
        <strong class="stat-value">{{ roster.archived_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">归并任务 / 待恢复 / 已完成</span>
        <strong class="stat-value">{{ mergeTasks.length }} / {{ blockedCount }} / {{ doneCount }}</strong>
      </article>
    </div>

    <p v-if="message" class="page-foot" :class="{ 'error-text': hasError }">{{ message }}</p>

    <h3>归档下架清册</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>导出ID</th>
          <th>任务编号</th>
          <th>关联API</th>
          <th>过期时间</th>
          <th>文件状态</th>
          <th>取数资格</th>
          <th>关联归并</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="entry in roster.entries ?? []" :key="entry.export_id"
            :data-status="entry.file_status">
          <td>{{ entry.export_id }}</td>
          <td>{{ entry.task_no ?? '—' }}</td>
          <td>{{ entry.api_name ?? '—' }}</td>
          <td>{{ entry.expire_at ?? '—' }}</td>
          <td>{{ entry.file_status }}</td>
          <td>{{ entry.eligible ? '可取数' : '已失效' }}</td>
          <td>{{ (entry.affected_merge_tasks ?? []).join('、') || '—' }}</td>
          <td class="row-actions">
            <button v-if="entry.file_status === '待下架'" class="link" type="button"
                    @click="archiveOne(entry.export_id)">
              执行下架
            </button>
            <span v-else class="page-desc">{{ entry.file_status === '已下架' ? '终态' : '在用' }}</span>
          </td>
        </tr>
        <tr v-if="!(roster.entries ?? []).length">
          <td colspan="8" class="empty-state">清册为空，请先在导出任务中发起导出</td>
        </tr>
      </tbody>
    </table>

    <h3 style="margin-top: 20px">当前具备取数资格的归并输入</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>导出ID</th>
          <th>任务编号</th>
          <th>关联API</th>
          <th>过期时间</th>
          <th>文件状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="source in sources" :key="source.export_id">
          <td>{{ source.export_id }}</td>
          <td>{{ source.task_no ?? '—' }}</td>
          <td>{{ source.api_name ?? '—' }}</td>
          <td>{{ source.expire_at ?? '—' }}</td>
          <td>{{ source.file_status }}</td>
        </tr>
        <tr v-if="!sources.length">
          <td colspan="5" class="empty-state">暂无在有效期内的导出文件</td>
        </tr>
      </tbody>
    </table>

    <h3 style="margin-top: 20px">登记归并任务</h3>
    <form class="filter-bar" @submit.prevent="registerTask">
      <label class="filter-item">
        <span>归并规则ID</span>
        <input v-model.number="registerForm.rule_id" type="number" min="1" required />
      </label>
      <label class="filter-item">
        <span>主侧导出ID</span>
        <input v-model.number="registerForm.primary_export_id" type="number" min="1" required />
      </label>
      <label class="filter-item">
        <span>从侧导出ID</span>
        <input v-model.number="registerForm.secondary_export_id" type="number" min="1" required />
      </label>
      <button class="btn primary" type="submit">登记归并</button>
    </form>

    <h3 style="margin-top: 20px">归并任务执行单</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>任务编号</th>
          <th>规则</th>
          <th>主侧来源 / 读取状态</th>
          <th>从侧来源 / 读取状态</th>
          <th>任务状态</th>
          <th>可否交付</th>
          <th>归并产物</th>
          <th>可继续操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in mergeTasks" :key="task.task_id" :data-status="task.status">
          <td>{{ task.task_no }}</td>
          <td>{{ task.rule_no ?? task.rule_id }}</td>
          <td>
            [{{ task.primary?.export_id }}] {{ task.primary?.api_name ?? '' }}
            <br /><small :class="sideClass(task.primary?.read_state)">{{ task.primary?.read_state }}</small>
          </td>
          <td>
            [{{ task.secondary?.export_id }}] {{ task.secondary?.api_name ?? '' }}
            <br /><small :class="sideClass(task.secondary?.read_state)">{{ task.secondary?.read_state }}</small>
          </td>
          <td>{{ task.status }}</td>
          <td>{{ task.deliverable ? '可交付' : '受阻' }}</td>
          <td><small>{{ task.artifact_path ? '已关联' : '未产出' }}</small></td>
          <td class="row-actions">
            <button v-if="task.status !== '已完成'" class="link" type="button"
                    @click="startTask(task.task_id)">读取主侧</button>
            <button v-if="task.primary?.read_state === '读取完成' && task.status !== '已完成'"
                    class="link" type="button" @click="readSecondary(task.task_id)">
              读取从侧
            </button>
            <button class="link" type="button" @click="checkDelivery(task.task_id)">核对交付</button>
            <button v-if="task.status === '待恢复'" class="link" type="button"
                    @click="openRecover(task)">
              补来源恢复
            </button>
          </td>
        </tr>
        <tr v-if="!mergeTasks.length">
          <td colspan="8" class="empty-state">暂无归并任务</td>
        </tr>
      </tbody>
    </table>

    <div v-if="recoverTarget" class="modal-backdrop" @click.self="recoverTarget = null">
      <form class="modal" @submit.prevent="submitRecover">
        <h3>为 {{ recoverTarget.task_no }} 补来源</h3>
        <p class="page-desc">
          受阻侧：{{ (recoverTarget.blocked_sides ?? []).join('、') || '—' }}
        </p>
        <label class="create-field">
          <span>补哪一侧</span>
          <select v-model="recoverForm.side" required>
            <option value="primary">主侧 primary</option>
            <option value="secondary">从侧 secondary</option>
          </select>
        </label>
        <label class="create-field">
          <span>替补导出文件ID（须在取数有效期内）</span>
          <input v-model.number="recoverForm.replacement_export_id" type="number" min="1" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="recoverTarget = null">取消</button>
          <button class="btn primary" type="submit">确认恢复</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  archiveExportFile,
  checkMergeDelivery,
  executeDecommissionRoster,
  loadDecommissionRoster,
  listMergeSources,
  listMergeTasks,
  readMergeSecondary,
  recoverMergeSource,
  registerMergeTask,
  startMergeTask,
} from '@/api/backend-service'

type RosterEntry = {
  export_id: number
  task_no?: string
  api_name?: string
  expire_at?: string
  file_status: string
  eligible: boolean
  affected_merge_tasks?: string[]
}

type MergeTask = {
  task_id: number
  task_no: string
  rule_id: number
  rule_no?: string
  status: string
  deliverable: boolean
  artifact_path?: string | null
  blocked_sides?: string[]
  primary?: { export_id: number; api_name?: string; read_state: string }
  secondary?: { export_id: number; api_name?: string; read_state: string }
}

type EligibleSource = {
  export_id: number
  task_no?: string
  api_name?: string
  expire_at?: string
  file_status: string
}

const roster = ref<{ total?: number; due_count?: number; archived_count?: number; entries?: RosterEntry[] }>({})
const sources = ref<EligibleSource[]>([])
const mergeTasks = ref<MergeTask[]>([])
const message = ref('')
const hasError = ref(false)
const registerForm = ref({ rule_id: 1, primary_export_id: 1, secondary_export_id: 1 })
const recoverTarget = ref<MergeTask | null>(null)
const recoverForm = ref({ side: 'secondary', replacement_export_id: 1 })

const blockedCount = computed(() => mergeTasks.value.filter((t) => t.status === '待恢复').length)
const doneCount = computed(() => mergeTasks.value.filter((t) => t.status === '已完成').length)

function sideClass(state?: string): string {
  if (state === '需恢复') return 'error-text'
  if (state === '读取完成') return ''
  return 'page-desc'
}

function notify(text: string, error = false): void {
  message.value = text
  hasError.value = error
}

async function reload(): Promise<void> {
  try {
    const [rosterPayload, sourcePayload, taskPayload] = await Promise.all([
      loadDecommissionRoster(),
      listMergeSources(),
      listMergeTasks(),
    ])
    roster.value = rosterPayload as typeof roster.value
    sources.value = sourcePayload.items as EligibleSource[]
    mergeTasks.value = taskPayload.items as MergeTask[]
  } catch (error) {
    notify(error instanceof Error ? error.message : '数据加载失败', true)
  }
}

async function executeRoster(): Promise<void> {
  try {
    const result = (await executeDecommissionRoster()) as { executed_count: number }
    notify(`下架清册执行完成，共处理 ${result.executed_count} 份到期文件`)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '清册执行失败', true)
  }
}

async function archiveOne(exportId: number): Promise<void> {
  try {
    await archiveExportFile(exportId)
    notify(`导出文件 ${exportId} 已归档下架，取数资格失效`)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '下架失败', true)
  }
}

async function registerTask(): Promise<void> {
  try {
    await registerMergeTask(
      registerForm.value.rule_id,
      registerForm.value.primary_export_id,
      registerForm.value.secondary_export_id,
    )
    notify('归并任务已登记，两侧来源均在取数有效期内')
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '归并登记失败', true)
  }
}

async function startTask(taskId: number): Promise<void> {
  try {
    await startMergeTask(taskId)
    notify('主侧来源读取完成')
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '主侧读取失败', true)
    await reload()
  }
}

async function readSecondary(taskId: number): Promise<void> {
  try {
    await readMergeSecondary(taskId)
    notify('两侧来源读取完成，归并产物已生成并关联')
  } catch (error) {
    notify(error instanceof Error ? error.message : '从侧读取失败，任务转待恢复', true)
  }
  await reload()
}

async function checkDelivery(taskId: number): Promise<void> {
  try {
    const result = (await checkMergeDelivery(taskId)) as {
      deliverable: boolean
      message: string
      missing_sources?: { side: string; reason?: string }[]
    }
    if (result.deliverable) {
      notify(result.message)
    } else {
      const missing = (result.missing_sources ?? [])
        .map((m) => `${m.side}（${m.reason ?? '来源未读取'}）`)
        .join('、')
      notify(`${result.message}：${missing}`, true)
    }
  } catch (error) {
    notify(error instanceof Error ? error.message : '交付核对失败', true)
  }
}

function openRecover(task: MergeTask): void {
  recoverTarget.value = task
  recoverForm.value = {
    side: task.blocked_sides?.[0] ?? 'secondary',
    replacement_export_id: 1,
  }
}

async function submitRecover(): Promise<void> {
  if (!recoverTarget.value) return
  try {
    const result = (await recoverMergeSource(
      recoverTarget.value.task_id,
      recoverForm.value.side as 'primary' | 'secondary',
      recoverForm.value.replacement_export_id,
    )) as { message?: string }
    recoverTarget.value = null
    notify(result.message ?? '来源已补齐')
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '恢复失败', true)
  }
}

onMounted(reload)
</script>
