import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
      '/webhook': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
      '/sma-health': {
        target: 'http://127.0.0.1:8003',
        changeOrigin: true,
        rewrite: (path) => path.replace('/sma-health', '/health'),
      },
      '/tas': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/events': {
        target: 'http://localhost:8010',
        changeOrigin: true,
      },
    },
  },
})
