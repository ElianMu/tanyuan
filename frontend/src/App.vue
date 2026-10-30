<template>
  <div class="app">
    <!-- 左侧边栏 -->
    <aside class="sidebar glass-card">
      <div class="logo">
        <el-icon :size="22" color="#165DFF"><MagicStick /></el-icon>
        <span class="logo-text">探渊</span>
        <span class="logo-sub">多智能体数据分析</span>
        <el-switch
          v-model="store.theme"
          class="theme-switch"
          inline-prompt
          active-icon="Moon"
          inactive-icon="Sunny"
          @change="toggleTheme"
        />
      </div>

      <!-- 统计卡片 -->
      <div class="stats">
        <div class="stat running">
          <span class="num">{{ stats.running }}</span>
          <span class="label">运行中</span>
        </div>
        <div class="stat completed">
          <span class="num">{{ stats.completed }}</span>
          <span class="label">已完成</span>
        </div>
        <div class="stat failed">
          <span class="num">{{ stats.failed }}</span>
          <span class="label">失败</span>
        </div>
      </div>

      <!-- 会话列表 -->
      <div class="section-title">会话列表</div>
      <el-scrollbar class="session-scroll">
        <el-empty v-if="!store.sessions.length" description="暂无会话" :image-size="60" />
        <div
          v-for="s in store.sessions"
          :key="s.thread_id"
          class="session-item"
          :class="{ active: s.thread_id === store.currentThreadId }"
          @click="onSwitch(s.thread_id)"
        >
          <div class="session-head">
            <el-tag :type="statusTagType(s.status)" size="small">{{ statusText(s.status) }}</el-tag>
            <span class="session-time">{{ s.created_at.slice(5, 16) }}</span>
          </div>
          <div class="session-query">{{ s.query }}</div>
          <div class="session-actions">
            <el-button link size="small" @click.stop="rerun(s.thread_id)">
              <el-icon><RefreshRight /></el-icon>重跑
            </el-button>
            <el-button link size="small" type="danger" @click.stop="onDelete(s.thread_id)">
              <el-icon><Delete /></el-icon>删除
            </el-button>
          </div>
        </div>
      </el-scrollbar>

      <!-- 快捷模板 -->
      <div class="section-title">快捷模板</div>
      <div class="templates">
        <el-tag
          v-for="t in QUICK_TEMPLATES"
          :key="t.label"
          class="template-tag"
          effect="plain"
          @click="fillTemplate(t.value)"
        >
          {{ t.label }}
        </el-tag>
      </div>
    </aside>

    <!-- 中央主区域 -->
    <main class="main">
      <EventTree :session="session" @rerun="rerun" @terminate="terminate" />
      <ResultPreview :session="session" />
    </main>

    <!-- 右侧文件面板 -->
    <aside class="file glass-card">
      <FilePanel :session="session" :ensure-session="ensureDraftSession" />
    </aside>

    <!-- 底部任务输入通栏 -->
    <footer class="taskbar">
      <TaskInput @submit="startTask" />
    </footer>

    <!-- Toast -->
    <div class="toasts">
      <transition-group name="toast">
        <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.type">
          <el-icon><component :is="toastIcon(t.type)" /></el-icon>
          <span>{{ t.message }}</span>
        </div>
      </transition-group>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import TaskInput from './components/TaskInput.vue'
import EventTree from './components/EventTree.vue'
import FilePanel from './components/FilePanel.vue'
import ResultPreview from './components/ResultPreview.vue'
import { createSession, submitTask, cancelTask, connectWS } from './api'
import {
  store,
  addSession,
  getSession,
  switchSession,
  deleteSession,
  toggleTheme,
  sessionStats,
  currentSession,
  toast,
  QUICK_TEMPLATES,
} from './store'

const stats = computed(() => sessionStats())
const session = computed(() => currentSession())

// threadId -> WebSocket，便于终止/删除时关闭
const wsMap = {}

function onEvent(session, ev) {
  // 重连回放与实时推送可能重叠，按 seq 去重
  if (ev.seq <= session.last_seq) return
  session.last_seq = ev.seq
  session.events.push(ev)
  if (ev.event === 'session_created') {
    session.session_dir = ev.data?.path || session.session_dir
  } else if (ev.event === 'task_result') {
    session.status = ev.data?.status || 'completed'
    session.result = ev.data?.result || ''
    toast(session.status === 'completed' ? 'success' : 'warning', session.status === 'completed' ? '任务完成' : '任务结束，报告未完全达标')
  } else if (ev.event === 'error') {
    session.status = 'failed'
    toast('error', ev.message || '任务执行失败')
  } else if (ev.event === 'cancelled') {
    session.status = 'cancelled'
  }
}

function watchSession(session) {
  const threadId = session.thread_id
  const ws = connectWS(threadId, session.last_seq, (ev) => onEvent(session, ev))
  // 任务未结束时断线自动重连，从 last_seq 之后继续回放
  ws.onclose = () => {
    if (wsMap[threadId] !== ws) return
    delete wsMap[threadId]
    if (session.status === 'running') setTimeout(() => getSession(threadId) && watchSession(session), 1000)
  }
  wsMap[threadId] = ws
}

// 上传需在任务启动前完成：无可用的未启动会话时新建一个
async function ensureDraftSession() {
  const current = currentSession()
  if (current && current.status === 'created') return current
  const res = await createSession()
  const draft = addSession(res.thread_id, '', false, 'created', res.path)
  watchSession(draft)
  return draft
}

async function startTask(query, requirePdf) {
  // 后端按问题文本判定交付物，勾选 PDF 时显式写入要求
  const text = requirePdf && !/pdf/i.test(query) ? `${query}（请生成 PDF 报告）` : query
  try {
    const s = await ensureDraftSession()
    s.query = text
    s.require_pdf = requirePdf
    await submitTask(text, s.thread_id)
    s.status = 'running'
    toast('info', `任务已提交：${s.thread_id.slice(0, 8)}`)
  } catch (e) {
    toast('error', e.message)
  }
}

// 后端每个会话只受理一次任务，重跑即用原问题新建会话
function rerun(threadId) {
  const s = getSession(threadId)
  if (!s || !s.query) return
  switchSession('')
  startTask(s.query, s.require_pdf)
}

async function terminate(threadId) {
  const s = getSession(threadId)
  if (!s) return
  try {
    const res = await cancelTask(threadId)
    s.status = res.status
    toast('warning', '任务已终止')
  } catch (e) {
    toast('error', e.message)
  }
}

function onSwitch(threadId) {
  switchSession(threadId)
}

function onDelete(threadId) {
  const ws = wsMap[threadId]
  delete wsMap[threadId]
  if (ws) ws.close()
  deleteSession(threadId)
  toast('info', '会话已删除')
}

function fillTemplate(value) {
  store.pendingQuery = value
}

function statusText(status) {
  return { created: '待提交', running: '运行中', completed: '已完成', incomplete: '未达标', failed: '失败', cancelled: '已终止' }[status] || status
}
function statusTagType(status) {
  return { running: 'primary', completed: 'success', incomplete: 'warning', failed: 'danger', cancelled: 'info' }[status] || 'info'
}
function toastIcon(type) {
  return { success: 'CircleCheck', error: 'CircleClose', warning: 'Warning', info: 'InfoFilled' }[type] || 'InfoFilled'
}
</script>

<style scoped>
.app {
  display: grid;
  grid-template-rows: 1fr auto;
  grid-template-columns: 220px 1fr 320px;
  grid-template-areas:
    'sidebar main file'
    'taskbar taskbar taskbar';
  height: 100vh;
  gap: 12px;
  padding: 12px;
  background: var(--bg);
}
.sidebar {
  grid-area: sidebar;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}
.main {
  grid-area: main;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
  min-width: 0;
}
.file {
  grid-area: file;
  padding: 12px;
  overflow: hidden;
}
.taskbar {
  grid-area: taskbar;
}

.logo {
  display: flex;
  align-items: center;
  gap: 6px;
}
.logo-text {
  font-size: 18px;
  font-weight: 700;
  color: var(--primary);
}
.logo-sub {
  font-size: 11px;
  color: var(--text-secondary);
  flex: 1;
}
.theme-switch {
  margin-left: auto;
}

.stats {
  display: flex;
  gap: 8px;
}
.stat {
  flex: 1;
  border-radius: 8px;
  padding: 8px;
  text-align: center;
  background: var(--bg);
}
.stat .num {
  font-size: 20px;
  font-weight: 700;
  display: block;
}
.stat .label {
  font-size: 11px;
  color: var(--text-secondary);
}
.stat.running .num {
  color: var(--primary);
}
.stat.completed .num {
  color: var(--success);
}
.stat.failed .num {
  color: var(--danger);
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}
.session-scroll {
  flex: 1;
  min-height: 0;
}
.session-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}
.session-item:hover {
  border-color: var(--primary);
}
.session-item.active {
  border-color: var(--primary);
  background: rgba(22, 93, 255, 0.06);
}
.session-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.session-time {
  font-size: 11px;
  color: var(--text-secondary);
}
.session-query {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.templates {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.template-tag {
  cursor: pointer;
}

.toasts {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.toast {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  border-radius: 8px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  font-size: 13px;
}
.toast.success {
  border-left: 3px solid var(--success);
}
.toast.error {
  border-left: 3px solid var(--danger);
}
.toast.warning {
  border-left: 3px solid var(--warning);
}
.toast.info {
  border-left: 3px solid var(--primary);
}
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>
