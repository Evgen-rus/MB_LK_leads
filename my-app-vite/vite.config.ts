import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Берём переменные окружения из корня монорепозитория (../.env)
  // Чтобы import.meta.env.VITE_* читались из корневого .env
  envDir: '..',
})
