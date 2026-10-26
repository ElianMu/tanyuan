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
        <EventNode :node="tree" />
      </div>
    </el-scrollbar>

    <!-- 反思校验卡片 -->
    <div v-if="session && session.result" class="reflection glass-card">
      <div class="reflection-title">
        <el-icon><CircleCheck /></el-icon>
        <span>反思校验</span>
        <span class="rounds">自动纠错 {{ reflection.rounds }} 轮</span>
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

// 工具名 → 归属
const TOOL_OWNER = {
  internet_search: '网络检索助手',
  list_sql_tables: '数据库查询助手',
  get_table_data: '数据库查询助手',
  execute_sql_query: '数据库查询助手',
  search_knowledge_base: '内部知识库助手',
  generate_markdown: '__main__',
  convert_md_to_pdf: '__main__',
  read_file_content: '__main__',
  write_todos: '__main__',
  ls: '__main__',
  glob: '__main__',
}
const HIDDEN_TOOLS = new Set(['write_todos', 'ls', 'glob'])
const PLACEHOLDERS = ['待补充', 'TODO', '等待子任务', '占位', '（略）']

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

const tree = computed(() => {
  const rootNode = { type: 'main', label: '主智能体', children: [] }
  const assistants = {}
  for (const ev of props.session?.events || []) {
    if (ev.event === 'session_created') {
      rootNode.children.push({
        type: 'session',
        label: '会话已创建',
        children: [],
        timestamp: formatTime(ev.timestamp),
      })
    } else if (ev.event === 'assistant_call') {
      const name = ev.data?.assistant_name || '子智能体'
      const meta = SUB_AGENT_META[name] || {}
      const node = { type: 'assistant', label: name, color: meta.color, icon: meta.icon, children: [] }
      rootNode.children.push(node)
      assistants[name] = node
    } else if (ev.event === 'tool_start') {
      const toolName = ev.data?.tool_name || '工具'
      if (HIDDEN_TOOLS.has(toolName)) continue
      const args = ev.data?.args || {}
      const node = {
        type: 'tool',
        label: toolName,
        children: [],
        args,
        argsSummary: summarizeArgs(args),
        timestamp: formatTime(ev.timestamp),
      }
      const owner = TOOL_OWNER[toolName]
      if (owner && owner !== '__main__' && assistants[owner]) {
        assistants[owner].children.push(node)
      } else {
        rootNode.children.push(node)
      }
    } else if (ev.event === 'task_result') {
      rootNode.children.push({ type: 'result', label: '任务完成', children: [] })
    } else if (ev.event === 'error') {
      rootNode.children.push({ type: 'error', label: ev.message || '执行失败', children: [] })
    }
  }
  return rootNode
})

// 反思校验（客户端近似：从事件+结果计算 H1~H4）
const reflection = computed(() => {
  const s = props.session
  const result = s?.result || ''
  const wordCount = result.length
  const hasPlaceholder = PLACEHOLDERS.some((k) => result.includes(k))
  const pdfProduced = (s?.events || []).some(
    (e) => e.event === 'tool_start' && e.data?.tool_name === 'convert_md_to_pdf'
  )
  const calledSources = [...new Set((s?.events || []).filter((e) => e.event === 'assistant_call').map((e) => e.data?.assistant_name))]
  const rounds = (s?.events || []).filter((e) => e.event === 'tool_start' && e.data?.tool_name === 'generate_markdown').length

  return {
    rounds,
    items: [
      { label: '字数', pass: wordCount >= 1000, detail: `${wordCount} / 1000` },
      { label: '占位符', pass: !hasPlaceholder, detail: hasPlaceholder ? '存在占位符' : '无占位符' },
      {
        label: '格式',
        pass: !s?.require_pdf || pdfProduced,
        detail: s?.require_pdf ? (pdfProduced ? '已生成 PDF' : '未生成 PDF') : '非 PDF 任务',
      },
      { label: '数据源', pass: calledSources.length > 0, detail: calledSources.join('、') || '未调用' },
    ],
  }
})

const isRunning = computed(() => props.session?.status === 'running')
const progressPercent = computed(() => {
  if (!props.session) return 0
  if (props.session.status === 'completed') return 100
  if (props.session.status === 'failed') return 100
  return Math.min(90, 10 + (props.session.events || []).length * 5)
})
const progressStatus = computed(() => {
  if (props.session?.status === 'completed') return 'success'
  if (props.session?.status === 'failed') return 'exception'
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
