import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Port 8080 is a bad default for the dev proxy: the local masking gateway
// publishes there (see compose.yaml and docs/current-endpoints.md), and it is
// also the default port for Jenkins, Tomcat, and similar tools. Pointing the
// proxy at an occupied port makes the console fail with whatever that service
// answers — a confusing 403 or 401 that looks like an arena bug.
// Override with ARENA_API_PORT or ARENA_API_TARGET when needed.
const target =
  process.env.ARENA_API_TARGET ||
  `http://127.0.0.1:${process.env.ARENA_API_PORT || 8099}`

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target,
        changeOrigin: false,
        // Surface a dead or wrong target as a clear message instead of letting
        // an unrelated service on that port answer for the arena.
        configure: proxy => {
          proxy.on('error', error => {
            console.error(
              `\n[arena] /api proxy to ${target} failed: ${error.message}\n` +
                '  Start the arena API there, or set ARENA_API_PORT / ARENA_API_TARGET.\n',
            )
          })
        },
      },
    },
  },
})
