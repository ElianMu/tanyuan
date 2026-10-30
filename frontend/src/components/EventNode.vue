<template>
  <div class="event-node">
    <div class="label" @click="toggle">
      <el-icon v-if="node.children && node.children.length" class="arrow" :class="{ expanded }">
        <CaretRight />
      </el-icon>
      <span v-else class="arrow leaf">·</span>

      <!-- 子智能体：专属配色 + 图标 -->
      <el-icon v-if="node.icon" class="node-icon" :size="14" :color="node.color">
        <component :is="node.icon" />
      </el-icon>

      <span
        class="badge"
        :class="[node.type, { pulse: node.type === 'running', failed: node.failed }]"
        :style="node.color ? { color: node.color, borderColor: node.color } : {}"
      >
        {{ node.label }}<template v-if="node.failed"> · 失败</template>
      </span>

      <span v-if="node.timestamp" class="time">{{ node.timestamp }}</span>
      <span v-if="node.type === 'tool'" class="tool-meta">{{ node.argsSummary }}</span>
    </div>

    <!-- 工具调用展开的完整参数 -->
    <div v-if="expanded && node.type === 'tool' && node.args" class="tool-args collapse-transition">
      <el-button link size="small" @click.stop="toggleArgs">查看参数</el-button>
      <pre v-if="showArgs">{{ JSON.stringify(node.args, null, 2) }}</pre>
    </div>

    <div v-if="expanded && node.children && node.children.length" class="children collapse-transition">
      <EventNode v-for="(child, i) in node.children" :key="i" :node="child" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({ node: Object })
const expanded = ref(true)
const showArgs = ref(false)
function toggle() {
  expanded.value = !expanded.value
}
function toggleArgs() {
  showArgs.value = !showArgs.value
}
</script>

<style scoped>
.event-node {
  margin-left: 14px;
}
.label {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 0;
  cursor: pointer;
}
.arrow {
  width: 14px;
  color: var(--text-secondary);
  transition: transform 0.2s;
}
.arrow.expanded {
  transform: rotate(90deg);
}
.arrow.leaf {
  color: #ccc;
}
.node-icon {
  flex-shrink: 0;
}
.badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 13px;
  border: 1px solid transparent;
}
.badge.main {
  background: #e8f1ff;
  color: #165dff;
}
.badge.assistant {
  background: var(--card-bg);
  border-style: solid;
}
.badge.tool {
  background: var(--bg);
  color: var(--text-secondary);
}
.badge.session {
  background: #f3e5f5;
  color: #6a1b9a;
}
.badge.result {
  background: #e8f5e9;
  color: #1b5e20;
}
.badge.tool.failed {
  background: #ffebee;
  color: #c62828;
}
.badge.error {
  background: #ffebee;
  color: #c62828;
}
.time {
  font-size: 11px;
  color: var(--text-secondary);
}
.tool-meta {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}
.tool-args {
  margin-left: 18px;
  padding: 4px 0;
}
.tool-args pre {
  font-size: 11px;
  background: var(--bg);
  border-radius: 4px;
  padding: 8px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
}
.children {
  border-left: 1px dashed var(--border);
}
</style>
