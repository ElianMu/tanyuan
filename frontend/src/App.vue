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
      <FilePanel :session="session" />
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
import { submitTask, connectWS } from './api'
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

// threadId -> WebSocket，便于终止/重跑时关闭
const wsMap = {}

function onEvent(session, ev) {
  session.events.push(ev)
  if (ev.event === 'session_created') {
    session.session_dir = ev.data?.path || ''
  } else if (ev.event === 'task_result') {
    session.status = 'completed'
    session.result = ev.data?.result || ''
    toast('success', '任务完成')
  } else if (ev.event === 'error') {
    session.status = 'failed'
    toast('error', ev.message || '任务执行失败')
  }
}

function startTask(query, requirePdf) {
  const threadId = crypto.randomUUID().replace(/-/g, '')
  const session = addSession(threadId, query, requirePdf)
  wsMap[threadId] = connectWS(threadId, (ev) => onEvent(session, ev))
  submitTask(query, threadId).then(() =>
    toast('info', `会话已创建：${threadId.slice(0, 8)}`)
  )
}

function rerun(threadId) {
  const s = getSession(threadId)
  if (!s) return
  s.events = []
  s.status = 'running'
  s.result = ''
  s.session_dir = ''
  s.hard_rounds = []
  if (wsMap[threadId]) {
    try {
      wsMap[threadId].close()
    } catch {}
  }
  wsMap[threadId] = connectWS(threadId, (ev) => onEvent(s, ev))
  submitTask(s.query, threadId)
  toast('info', '任务已重新执行')
}

function terminate(threadId) {
  const s = getSession(threadId)
  if (!s) return
  if (wsMap[threadId]) {
    try {
      wsMap[threadId].close()
    } catch {}
  }
  s.status = 'failed'
  toast('warning', '任务已终止')
}

function onSwitch(threadId) {
  switchSession(threadId)
}

function onDelete(threadId) {
  deleteSession(threadId)
  toast('info', '会话已删除')
}

function fillTemplate(value) {
  store.pendingQuery = value
}

function statusText(status) {
  return { running: '运行中', completed: '已完成', failed: '失败' }[status] || status
}
function statusTagType(status) {
  return { running: 'primary', completed: 'success', failed: 'danger' }[status] || 'info'
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
