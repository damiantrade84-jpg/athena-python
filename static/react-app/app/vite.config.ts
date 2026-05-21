import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

const buildId = process.env.VITE_APP_BUILD_ID ?? new Date().toISOString()

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
  plugins: [
    inspectAttr(),
    react(),
    {
      name: 'athena-frontend-build-meta',
      transformIndexHtml(html) {
        return html.replace(
          '<head>',
          `<head>\n    <meta name="athena-frontend-build" content="${buildId}" />`,
        )
      },
    },
  ],
  define: {
    'import.meta.env.VITE_APP_BUILD_ID': JSON.stringify(buildId),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: '../../',
    emptyOutDir: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
