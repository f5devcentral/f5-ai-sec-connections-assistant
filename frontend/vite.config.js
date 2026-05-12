import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/analyze': 'http://127.0.0.1:8000',
      '/generate-yaml': 'http://127.0.0.1:8000',
      '/generate-profile-yaml': 'http://127.0.0.1:8000',
      '/validate-yaml': 'http://127.0.0.1:8000',
      '/validate-profile-yaml': 'http://127.0.0.1:8000',
      '/create-provider': 'http://127.0.0.1:8000',
      '/delete-provider': 'http://127.0.0.1:8000',
      '/test-provider-prompt': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000'
    }
  }
})
