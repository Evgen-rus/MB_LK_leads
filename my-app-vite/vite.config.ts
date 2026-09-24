import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Берём переменные окружения из корня монорепозитория (../.env)
  // Чтобы import.meta.env.VITE_* читались из корневого .env
  envDir: '..',
  // IPv4 loopback: туннель на 127.0.0.1 не видит сервер, если Vite слушает только ::1.
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
