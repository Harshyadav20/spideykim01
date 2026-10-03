import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev:  proxy API/media to the FastAPI backend on :8000
// Build: emit straight into backend/static so FastAPI serves the SPA itself
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/media': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
})
