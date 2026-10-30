<template>
  <div class="file-panel">
    <div class="panel-title">
      <el-icon><FolderOpened /></el-icon>
      <span>文件</span>
    </div>

    <el-tabs v-model="activeTab" class="tabs">
      <!-- Tab1：上传文件 -->
      <el-tab-pane label="上传文件" name="upload">
        <el-upload
          drag
          multiple
          :auto-upload="false"
          :on-change="onFileChange"
          :file-list="uploadList"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或<em>点击上传</em></div>
        </el-upload>
        <div class="upload-tip">附件需在提交任务前上传，将随下一次提交的任务一起分析</div>

        <div v-if="uploadList.length" class="upload-list">
          <div v-for="(f, i) in uploadList" :key="i" class="file-card">
            <el-icon :color="fileIconColor(f.name)"><Document /></el-icon>
            <span class="fname">{{ f.name }}</span>
            <span class="fsize">{{ formatSize(f.size) }}</span>
            <el-button link size="small" type="danger" @click="removeFile(i)">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-button type="primary" size="small" :loading="uploading" @click="doUpload">上传</el-button>
        </div>
      </el-tab-pane>

      <!-- Tab2：会话成果 -->
      <el-tab-pane label="会话成果" name="result">
        <el-empty v-if="!session || !resultFiles.length" description="暂无成果文件" :image-size="70" />
        <div v-else class="result-list">
          <div v-for="f in resultFiles" :key="f.path" class="file-card">
            <el-icon :color="f.type === 'file' && f.name.endsWith('.pdf') ? '#F56C6C' : '#165DFF'">
              <Document />
            </el-icon>
            <span class="fname">{{ f.name }}</span>
            <span class="fsize">{{ f.type === 'dir' ? '目录' : formatSize(f.size) }}</span>
            <el-button v-if="f.type === 'file'" link size="small" type="primary" @click="download(f)">
              <el-icon><Download /></el-icon>下载
            </el-button>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { uploadFiles, listFiles, downloadFile } from '../api'
import { toast } from '../store'

const props = defineProps({ session: Object, ensureSession: Function })
const activeTab = ref('upload')
const uploadList = ref([])
const uploading = ref(false)
const resultFiles = ref([])
const filesVersion = ref(0)

// 切换会话或任务结束（产出新文件）时刷新成果列表
watch(
  () => [props.session?.thread_id, props.session?.status],
  () => {
    if (props.session?.session_dir) refresh()
    else resultFiles.value = []
  },
  { immediate: true }
)

async function refresh() {
  if (!props.session?.session_dir) return
  try {
    const res = await listFiles(props.session.thread_id)
    resultFiles.value = res.files || []
  } catch (e) {
    toast('error', e.message)
  }
}

function onFileChange(file, fileList) {
  uploadList.value = fileList.map((f) => f.raw)
}

function removeFile(i) {
  uploadList.value.splice(i, 1)
}

async function doUpload() {
  if (!uploadList.value.length) return
  uploading.value = true
  try {
    const target = await props.ensureSession()
    const res = await uploadFiles(target.thread_id, uploadList.value)
    toast('success', `已上传 ${(res.files || []).length} 个文件`)
    uploadList.value = []
    refresh()
  } catch (e) {
    toast('error', e.message)
  } finally {
    uploading.value = false
  }
}

async function download(f) {
  // 越权由后端按 thread_id 校验，403 时提示原因
  let res
  try {
    res = await downloadFile(props.session.thread_id, f.path)
  } catch (e) {
    toast('error', e.message)
    return
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = f.name
  a.click()
  URL.revokeObjectURL(url)
}

function formatSize(size) {
  if (size == null) return ''
  if (size < 1024) return size + ' B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
  return (size / 1024 / 1024).toFixed(1) + ' MB'
}
function fileIconColor(name) {
  if (name.endsWith('.pdf')) return '#F56C6C'
  if (name.endsWith('.md') || name.endsWith('.txt')) return '#165DFF'
  if (name.endsWith('.xlsx') || name.endsWith('.xls')) return '#67C23A'
  return '#909399'
}
</script>

<style scoped>
.file-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 8px;
}
.tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.upload-tip {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}
.upload-list,
.result-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.file-card {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.fname {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fsize {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
}
</style>
