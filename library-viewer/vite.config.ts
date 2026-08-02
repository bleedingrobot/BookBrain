import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// GitHub Pages serves this repo at https://<user>.github.io/BookBrain/ —
// every asset URL needs that prefix or it 404s once deployed.
export default defineConfig({
  base: '/BookBrain/',
  plugins: [react(), tailwindcss()],
})
