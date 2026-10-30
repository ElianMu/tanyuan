// REST 封装（开发期经 Vite 代理转发到 FastAPI）

// 非 2xx 统一抛出后端 detail，便于界面提示 409/413/429 等真实原因
async function request(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {}
    throw new Error(detail)
  }
  return res
}

export async function createSession() {
  const res = await request('/api/session', { method: 'POST' })
  return res.json()
}

export async function submitTask(query, threadId) {
  const res = await request('/api/task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, thread_id: threadId }),
  })
  return res.json()
}

export async function cancelTask(threadId) {
  const res = await request(`/api/task/${threadId}/cancel`, { method: 'POST' })
  return res.json()
}

export async function uploadFiles(threadId, files) {
  const form = new FormData()
  form.append('thread_id', threadId)
  for (const f of files) form.append('files', f)
  const res = await request('/api/upload', { method: 'POST', body: form })
  return res.json()
}

// 文件接口均按会话校验，必须带 thread_id
export async function listFiles(threadId, path = '') {
  const params = new URLSearchParams({ thread_id: threadId, path })
  const res = await request(`/api/files?${params}`)
  return res.json()
}

export async function downloadFile(threadId, path) {
  const params = new URLSearchParams({ thread_id: threadId, path })
  return request(`/api/download?${params}`)
}

// WebSocket 连接；after 为已收到的最大 seq，服务端回放其后的历史事件，避免连接晚于事件时丢失
export function connectWS(threadId, after, onEvent) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/${threadId}?after=${after}`)
  ws.onmessage = (e) => {
    let data
    try {
      data = JSON.parse(e.data)
    } catch {
      return
    }
    if (data.type === 'monitor_event') onEvent(data)
  }
  return ws
}
