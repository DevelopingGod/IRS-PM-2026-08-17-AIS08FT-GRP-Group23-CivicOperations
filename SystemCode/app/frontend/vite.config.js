import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend runs on port 8000. During development the frontend forwards /api to it,
// so no address is hardcoded in the app. For a deployment set VITE_API_BASE instead.
// 127.0.0.1, not localhost: on Windows localhost tries IPv6 first and adds about 200 ms to every call.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': process.env.CIVICOPS_API || 'http://127.0.0.1:8000' } },
  // three.js is one large library, loaded on its own only by the landing page's 3D city.
  build: { chunkSizeWarningLimit: 650 },
})
