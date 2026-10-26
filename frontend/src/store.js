// 全局状态：会话列表 + 当前会话 + 主题 + Toast（用 reactive 单例共享）
import { reactive } from 'vue'

export const store = reactive({
  sessions: [], // [{ thread_id, query, require_pdf, status, created_at, events, session_dir, result }]
  currentThreadId: '',
  theme: localStorage.getItem('tanyuan-theme') || 'light',
  toasts: [], // [{ id, type, message }]
  pendingQuery: '', // 快捷模板填充到输入框的指令
})

// 统计：运行中 / 已完成 / 失败
export function sessionStats() {
  const stats = { running: 0, completed: 0, failed: 0 }
  for (const s of store.sessions) {
    if (stats[s.status] !== undefined) stats[s.status]++
  }
  return stats
}

export function getSession(threadId) {
  return store.sessions.find((s) => s.thread_id === threadId)
}

export function currentSession() {
  return getSession(store.currentThreadId) || null
}

export function addSession(threadId, query, requirePdf) {
  const session = {
    thread_id: threadId,
    query,
    require_pdf: requirePdf,
    status: 'running', // running | completed | failed
    created_at: new Date().toLocaleString('zh-CN'),
    events: [],
    session_dir: '',
    result: '',
    hard_rounds: [], // 反思校验记录（供反思卡片）
  }
  store.sessions.unshift(session)
  store.currentThreadId = threadId
  return session
}

export function switchSession(threadId) {
  store.currentThreadId = threadId
}

export function deleteSession(threadId) {
  const i = store.sessions.findIndex((s) => s.thread_id === threadId)
  if (i >= 0) store.sessions.splice(i, 1)
  if (store.currentThreadId === threadId) {
    store.currentThreadId = store.sessions[0]?.thread_id || ''
  }
}

export function toggleTheme() {
  store.theme = store.theme === 'light' ? 'dark' : 'light'
  localStorage.setItem('tanyuan-theme', store.theme)
  document.documentElement.setAttribute('data-theme', store.theme)
}

// 应用主题到 <html> 的 data-theme
export function applyTheme() {
  document.documentElement.setAttribute('data-theme', store.theme)
}

let toastSeq = 0
export function toast(type, message) {
  const id = ++toastSeq
  store.toasts.push({ id, type, message })
  setTimeout(() => {
    const i = store.toasts.findIndex((t) => t.id === id)
    if (i >= 0) store.toasts.splice(i, 1)
  }, 3000)
}

// 快捷模板指令
export const QUICK_TEMPLATES = [
  { label: '药品市场分析', value: '生成一份阿莫西林药品市场分析报告（PDF）' },
  { label: '销售周报', value: '根据销售数据生成一份本周销售周报' },
  { label: '库存盘点', value: '盘点当前库存，列出库存量低于阈值的药品' },
  { label: '药品清单查询', value: '列出所有抗生素类药品' },
]

// 子智能体专属配色 + 图标（供执行树渲染）
export const SUB_AGENT_META = {
  网络检索助手: { color: '#409EFF', icon: 'Search' },
  数据库查询助手: { color: '#67C23A', icon: 'DataLine' },
  内部知识库助手: { color: '#9254DE', icon: 'Collection' },
}
