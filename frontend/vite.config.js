import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',  // local dev
        changeOrigin: true,
      }
    }
  },
  // For Vercel — API calls go to Railway
  define: {
    __API_URL__: JSON.stringify(
      process.env.VITE_API_URL || 'http://localhost:8000'
    )
  }
})