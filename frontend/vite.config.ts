import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend serves this bundle from "/" and mounts the card SVGs at
// "/assets/cards/*". Keeping the bundle out of /assets avoids that collision
// (which made the page load with a 404 for its JS and CSS).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  build: { assetsDir: 'static' },
})
