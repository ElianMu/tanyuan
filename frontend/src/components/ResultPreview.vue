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

      <!-- PDF 预览（会话产出 PDF 时显示） -->
      <el-tab-pane v-if="pdfUrl" label="PDF 预览" name="pdf">
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

// 报告可能夹带网页检索内容：原始 HTML 一律转义，链接与图片仅允许 http(s)/站内地址
const escapeHtml = (text) =>
  text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const safeUrl = (href) => /^(https?:\/\/|#|\/(?!\/))/i.test(href || '')
marked.use({
  renderer: {
    html({ text }) {
      return escapeHtml(text)
    },
    link({ href, tokens }) {
      return safeUrl(href) ? false : this.parser.parseInline(tokens)
    },
    image({ href, text }) {
      return safeUrl(href) ? false : escapeHtml(text)
    },
  },
})

const renderedMarkdown = computed(() => marked.parse(props.session?.result || ''))

// 切换会话或任务结束时查找会话目录中的 PDF
watch(
  () => [props.session?.thread_id, props.session?.status],
  async () => {
    if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
    pdfUrl.value = ''
    if (props.session?.session_dir) await loadPdf(props.session.thread_id)
  },
  { immediate: true }
)

async function loadPdf(threadId) {
  try {
    const res = await listFiles(threadId)
    const pdf = (res.files || []).find((f) => f.type === 'file' && f.name.endsWith('.pdf'))
    if (pdf) {
      const r = await downloadFile(threadId, pdf.path)
      const blob = await r.blob()
      if (props.session?.thread_id === threadId) pdfUrl.value = URL.createObjectURL(blob)
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
