import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api 代理到后端 FastAPI(localhost:8000),前端只管调 /api/*
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8116', changeOrigin: true } },
  },
})
