<template>
  <div class="task-input glass-card">
    <div class="input-row">
      <el-input
        v-model="query"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 6 }"
        placeholder="请输入分析任务，如：列出所有抗生素类药品（Ctrl+Enter 提交）"
        @keydown.ctrl.enter="submit"
      />
      <el-dropdown @command="fillFromTemplate">
        <el-button class="tpl-btn">
          示例指令<el-icon class="el-icon--right"><ArrowDown /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="t in QUICK_TEMPLATES" :key="t.label" :command="t.value">
              {{ t.label }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
    <div class="input-foot">
      <el-checkbox v-model="requirePdf">强制生成 PDF 报告</el-checkbox>
      <el-button type="primary" :loading="submitting" :disabled="!query.trim()" @click="submit">
        <el-icon v-if="!submitting" class="el-icon--left"><Position /></el-icon>
        提交任务
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { store, QUICK_TEMPLATES } from '../store'

const emit = defineEmits(['submit'])
const query = ref('')
const requirePdf = ref(false)
const submitting = ref(false)

// 快捷模板填充（来自左侧边栏或下拉）
watch(
  () => store.pendingQuery,
  (v) => {
    if (v) {
      query.value = v
      store.pendingQuery = ''
    }
  }
)

function fillFromTemplate(value) {
  query.value = value
}

function submit() {
  if (!query.value.trim()) return
  submitting.value = true
  emit('submit', query.value.trim(), requirePdf.value)
  query.value = ''
  setTimeout(() => (submitting.value = false), 600)
}
</script>

<style scoped>
.task-input {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.input-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
}
.input-row :deep(.el-textarea) {
  flex: 1;
}
.tpl-btn {
  white-space: nowrap;
}
.input-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
