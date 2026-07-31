import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api 代理到后端 FastAPI(localhost:18000),前端只管调 /api/*
// 端口选 18000 而非 8000:Windows 上 8000 落在 TCP 动态端口范围内,会被其他程序当临时端口抢占
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:18000', changeOrigin: true } },
  },
})
