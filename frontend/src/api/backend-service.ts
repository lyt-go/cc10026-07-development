import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ========== 归档下架清册 ==========

export async function loadDecommissionRoster(): Promise<Record<string, unknown>> {
  const response = await request('/api/archive/roster')
  if (!response.ok) throw new Error('归档下架清册读取失败')
  return await response.json()
}

export async function executeDecommissionRoster(): Promise<Record<string, unknown>> {
  const response = await request('/api/archive/execute', { method: 'POST' })
  if (!response.ok) throw new Error('下架清册执行失败')
  return await response.json()
}

export async function archiveExportFile(exportId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/archive/exports/${exportId}`, { method: 'POST' })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? '导出文件下架失败')
  }
  return await response.json()
}

// ========== 数据归并任务 ==========

export async function listMergeSources(): Promise<{ items: unknown[]; total: number }> {
  const response = await request('/api/merge/sources')
  if (!response.ok) throw new Error('归并可用来源读取失败')
  return await response.json()
}

export async function listMergeTasks(): Promise<{ items: unknown[]; total: number }> {
  const response = await request('/api/merge/tasks')
  if (!response.ok) throw new Error('归并任务读取失败')
  return await response.json()
}

export async function registerMergeTask(
  ruleId: number,
  primaryExportId: number,
  secondaryExportId: number,
): Promise<Record<string, unknown>> {
  const response = await request('/api/merge/tasks', {
    method: 'POST',
    body: JSON.stringify({
      rule_id: ruleId,
      primary_export_id: primaryExportId,
      secondary_export_id: secondaryExportId,
    }),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? '归并任务登记失败')
  }
  return await response.json()
}

export async function startMergeTask(taskId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/merge/tasks/${taskId}/start`, { method: 'POST' })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? '主侧来源读取失败')
  }
  return await response.json()
}

export async function readMergeSecondary(taskId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/merge/tasks/${taskId}/secondary`, { method: 'POST' })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? '从侧来源读取失败')
  }
  return await response.json()
}

export async function checkMergeDelivery(taskId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/merge/tasks/${taskId}/delivery`)
  if (!response.ok) throw new Error('归并交付核对失败')
  return await response.json()
}

export async function recoverMergeSource(
  taskId: number,
  side: 'primary' | 'secondary',
  replacementExportId: number,
): Promise<Record<string, unknown>> {
  const response = await request(`/api/merge/tasks/${taskId}/recover`, {
    method: 'POST',
    body: JSON.stringify({ side, replacement_export_id: replacementExportId }),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? '归并来源恢复失败')
  }
  return await response.json()
}
