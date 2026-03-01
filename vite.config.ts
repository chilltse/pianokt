import { reactRouter } from '@react-router/dev/vite'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
import devtoolsJson from 'vite-plugin-devtools-json'
import tsconfigPaths from 'vite-tsconfig-paths'

export default defineConfig({
  plugins: [devtoolsJson(), tailwindcss(), reactRouter(), tsconfigPaths()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('@supabase')) return 'vendor-supabase'
            if (id.includes('@tonejs/midi')) return 'vendor-midi'
            if (id.includes('jotai')) return 'vendor-jotai'
            if (id.includes('react-aria') || id.includes('radix-ui')) return 'vendor-ui'
            if (id.includes('react') || id.includes('react-dom')) return 'vendor-react'
          }
        },
      },
    },
  },
})
