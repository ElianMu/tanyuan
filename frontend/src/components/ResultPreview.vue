<template>
  <div class="result-preview glass-card">
    <el-tabs v-model="activeTab" class="tabs">
      <!-- 问答文本 -->
      <el-tab-pane label="问答文本" name="text">
        <el-empty v-if="!session || !session.result" description="暂无结果" :image-size="80" />
        <div v-else class="text-body">{{ session.result }}</div>
      </el-tab-pane>

      <!-- Markdown 预览 + 复制 -->
      <el-tab-pane label="Markdown 报告" name="md">
        <el-empty v-if="!session || !session.result" description="暂无报告" :image-size="80" />
        <div v-else class="md-wrap">
          <div class="md-toolbar">
            <span class="word-count">正文 {{ session.result.length }} 字</span>
            <el-button link size="small" type="primary" @click="copyMarkdown">
              <el-icon><CopyDocument /></el-icon>复制
            </el-button>
          </div>
          <div class="md-body" v-html="renderedMarkdown"></div>
        </div>
      </el-tab-pane>

      <!-- PDF 预览（仅 PDF 任务） -->
      <el-tab-pane v-if="session && session.require_pdf" label="PDF 预览" name="pdf">
        <div class="pdf-toolbar">
          <el-button link size="small" @click="zoom(0.8)"><el-icon><ZoomOut /></el-icon></el-button>
          <el-button link size="small" @click="zoom(1.2)"><el-icon><ZoomIn /></el-icon></el-button>
          <el-button link size="small" @click="zoom(1)">重置</el-button>
        </div>
        <el-empty v-if="!pdfUrl" description="PDF 生成中或不存在" :image-size="80" />
        <iframe v-else :src="pdfUrl" class="pdf-frame" :style="{ transform: `scale(${scale})` }"></iframe>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import { listFiles, downloadFile } from '../api'
import { toast } from '../store'

const props = defineProps({ session: Object })
const activeTab = ref('text')
const pdfUrl = ref('')
const scale = ref(1)

const renderedMarkdown = computed(() => {
  const md = props.session?.result || ''
  return marked.parse(md)
})

watch(
  () => props.session?.session_dir,
  async (dir) => {
    pdfUrl.value = ''
    if (dir) await loadPdf(dir)
  }
)

async function loadPdf(dir) {
  try {
    const res = await listFiles(dir)
    const pdf = (res.files || []).find((f) => f.type === 'file' && f.name.endsWith('.pdf'))
    if (pdf) {
      const r = await downloadFile(pdf.path)
      const blob = await r.blob()
      pdfUrl.value = URL.createObjectURL(blob)
    }
  } catch {}
}

function copyMarkdown() {
  navigator.clipboard.writeText(props.session?.result || '')
  toast('success', '报告已复制到剪贴板')
}

function zoom(v) {
  scale.value = v
}
</script>

<style scoped>
.result-preview {
  height: 40%;
  min-height: 180px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
}
.tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.text-body {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.7;
  max-height: 100%;
  overflow: auto;
}
.md-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.md-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
.word-count {
  font-size: 12px;
  color: var(--text-secondary);
}
.md-body {
  flex: 1;
  overflow: auto;
  font-size: 13px;
  line-height: 1.7;
  padding: 8px 0;
}
.md-body :deep(table) {
  border-collapse: collapse;
}
.md-body :deep(td),
.md-body :deep(th) {
  border: 1px solid var(--border);
  padding: 4px 8px;
}
.pdf-toolbar {
  display: flex;
  gap: 4px;
  padding-bottom: 4px;
}
.pdf-frame {
  width: 100%;
  height: calc(100% - 30px);
  border: none;
  transform-origin: top left;
}
</style>
