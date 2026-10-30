<template>
  <div class="event-tree glass-card">
    <!-- 全局控制栏 -->
    <div class="control-bar">
      <span class="sid" title="会话ID">
        <el-icon><Link /></el-icon>
        {{ session ? session.thread_id.slice(0, 8) : '未选择会话' }}
      </span>
      <el-progress
        class="progress"
        :percentage="progressPercent"
        :status="progressStatus"
        :stroke-width="6"
      />
      <el-tooltip content="折叠/展开全部">
        <el-button link size="small" @click="toggleAll">
          <el-icon><Fold v-if="allExpanded" /><Expand v-else /></el-icon>
        </el-button>
      </el-tooltip>
      <el-tooltip content="清空日志">
        <el-button link size="small" @click="clearLogs">
          <el-icon><Delete /></el-icon>
        </el-button>
      </el-tooltip>
      <el-tooltip content="终止任务">
        <el-button link size="small" type="danger" :disabled="!isRunning" @click="$emit('terminate', session.thread_id)">
          <el-icon><VideoPause /></el-icon>
        </el-button>
      </el-tooltip>
    </div>

    <!-- 执行树 -->
    <el-scrollbar class="tree-scroll">
      <el-empty v-if="!session || !tree.children.length" description="暂无执行日志，请提交任务" :image-size="80" />
      <div v-else class="tree-body">
        <EventNode :key="treeKey" :node="tree" />
      </div>
    </el-scrollbar>

    <!-- 反思校验卡片 -->
    <div v-if="reflection.rounds" class="reflection glass-card">
      <div class="reflection-title">
        <el-icon><CircleCheck /></el-icon>
        <span>反思校验</span>
        <span class="rounds">共 {{ reflection.rounds }} 轮</span>
      </div>
      <div class="reflection-items">
        <div
          v-for="item in reflection.items"
          :key="item.label"
          class="reflection-item"
          :class="{ fail: !item.pass }"
        >
          <span class="r-label">{{ item.label }}</span>
          <span class="r-detail">{{ item.detail }}</span>
          <el-icon v-if="item.pass"><CircleCheckFilled /></el-icon>
          <el-icon v-else class="fail-icon"><WarningFilled /></el-icon>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import EventNode from './EventNode.vue'
import { SUB_AGENT_META } from '../store'

const props = defineProps({ session: Object })
defineEmits(['rerun', 'terminate'])

const HIDDEN_TOOLS = new Set(['write_todos', 'ls', 'glob'])

const allExpanded = ref(true)
function toggleAll() {
  allExpanded.value = !allExpanded.value
  // 简单实现：通过重建树触发 EventNode 的 expanded 状态（此处用 key 刷新）
  treeKey.value++
}
const treeKey = ref(0)

function clearLogs() {
  if (props.session) props.session.events = []
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}
function summarizeArgs(args) {
  if (!args || typeof args !== 'object') return ''
  const parts = Object.entries(args).map(([k, v]) => {
    const s = typeof v === 'string' ? v : JSON.stringify(v)
    return s.length > 30 ? `${k}=${s.slice(0, 30)}…` : `${k}=${s}`
  })
  return parts.join(' · ')
}

// 按后端 id / parent_id 还原「主智能体 → 子智能体 → 工具」层级
const tree = computed(() => {
  const rootNode = { type: 'main', label: '主智能体', children: [] }
  const nodes = {}
  const attach = (node, parentId) => (nodes[parentId] || rootNode).children.push(node)
  for (const ev of props.session?.events || []) {
    const d = ev.data || {}
    if (ev.event === 'session_created') {
      if (!rootNode.children.some((n) => n.type === 'session')) {
        rootNode.children.push({ type: 'session', label: '会话已创建', children: [], timestamp: formatTime(ev.timestamp) })
      }
    } else if (ev.event === 'assistant_call') {
      const name = d.assistant_name || '子智能体'
      const meta = SUB_AGENT_META[name] || {}
      const node = { type: 'assistant', label: name, color: meta.color, icon: meta.icon, children: [], timestamp: formatTime(ev.timestamp) }
      nodes[d.id] = node
      attach(node, d.parent_id)
    } else if (ev.event === 'tool_start') {
      if (HIDDEN_TOOLS.has(d.tool_name)) continue
      const args = d.args || {}
      const node = {
        type: 'tool',
        label: d.tool_name || '工具',
        children: [],
        args,
        argsSummary: summarizeArgs(args),
        timestamp: formatTime(ev.timestamp),
      }
      nodes[d.id] = node
      attach(node, d.parent_id)
    } else if ((ev.event === 'tool_end' || ev.event === 'tool_error') && d.status === 'error' && nodes[d.id]) {
      nodes[d.id].failed = true
    } else if (ev.event === 'reflection') {
      const passed = !(d.hard_issues || []).length && !d.soft_issue
      rootNode.children.push({ type: passed ? 'result' : 'error', label: `反思校验第 ${d.round} 轮：${passed ? '通过' : '未通过'}`, children: [] })
    } else if (ev.event === 'task_result') {
      rootNode.children.push({ type: 'result', label: d.status === 'incomplete' ? '任务结束（未达标）' : '任务完成', children: [] })
    } else if (ev.event === 'error' || ev.event === 'cancelled') {
      rootNode.children.push({ type: 'error', label: ev.message || '执行失败', children: [] })
    }
  }
  return rootNode
})

// 反思校验：直接展示后端每轮 H1~H4 硬校验与语义审阅结果
const reflection = computed(() => {
  const rounds = (props.session?.events || []).filter((e) => e.event === 'reflection').map((e) => e.data || {})
  return {
    rounds: rounds.length,
    items: rounds.map((d) => {
      const issues = [...(d.hard_issues || []), ...(d.soft_issue ? [d.soft_issue] : [])]
      return {
        label: `第 ${d.round} 轮`,
        pass: !issues.length,
        detail: issues.length ? issues.join('；').slice(0, 120) : '硬校验与语义审阅通过',
      }
    }),
  }
})

const isRunning = computed(() => props.session?.status === 'running')
const progressPercent = computed(() => {
  if (!props.session) return 0
  if (['completed', 'incomplete', 'failed', 'cancelled'].includes(props.session.status)) return 100
  return Math.min(90, 10 + (props.session.events || []).length * 5)
})
const progressStatus = computed(() => {
  const status = props.session?.status
  if (status === 'completed') return 'success'
  if (status === 'incomplete') return 'warning'
  if (status === 'failed' || status === 'cancelled') return 'exception'
  return ''
})
</script>

<style scoped>
.event-tree {
  display: flex;
  flex-direction: column;
  padding: 12px;
  flex: 1;
  min-height: 0;
}
.control-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.sid {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.progress {
  flex: 1;
}
.tree-scroll {
  flex: 1;
  min-height: 0;
}
.tree-body {
  padding: 8px 0;
}

.reflection {
  margin-top: 8px;
  padding: 10px;
}
.reflection-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
}
.reflection-title .rounds {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
}
.reflection-items {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.reflection-item {
  flex: 1;
  min-width: 100px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--bg);
  font-size: 12px;
}
.reflection-item.fail {
  background: rgba(230, 162, 60, 0.15);
  color: var(--warning);
}
.reflection-item .r-detail {
  color: var(--text-secondary);
  font-size: 11px;
}
.reflection-item .fail-icon {
  color: var(--warning);
}
</style>
