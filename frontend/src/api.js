// REST 封装（开发期经 Vite 代理转发到 FastAPI）

export async function submitTask(query, threadId) {
  const res = await fetch('/api/task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, thread_id: threadId }),
  })
  return res.json()
}

export async function uploadFiles(threadId, files) {
  const form = new FormData()
  form.append('thread_id', threadId)
  for (const f of files) form.append('files', f)
  const res = await fetch('/api/upload', { method: 'POST', body: form })
  return res.json()
}

export async function listFiles(path) {
  const res = await fetch(`/api/files?path=${encodeURIComponent(path)}`)
  return res.json()
}

export async function downloadFile(path) {
  return fetch(`/api/download?path=${encodeURIComponent(path)}`)
}

// WebSocket 连接（开发期经 Vite 代理 /ws 到后端）
export function connectWS(threadId, onEvent) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/${threadId}`)
  ws.onmessage = (e) => {
    let data
    try {
      data = JSON.parse(e.data)
    } catch {
      return
    }
    if (data.type === 'monitor_event') onEvent(data)
  }
  ws.onclose = () => console.log('WS 已断开')
  return ws
}
