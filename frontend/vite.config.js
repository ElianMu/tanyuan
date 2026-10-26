import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发代理：/api、/ws 转发到 FastAPI（8000 端口）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
