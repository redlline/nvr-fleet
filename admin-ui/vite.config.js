import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // manualChunks must be a function in Vite 8 (rolldown bundler).
        // Splits hls.js (~500 kB) into its own chunk so it is only loaded
        // on WatchPage / ArchiveTab, eliminating the chunk size warning.
        manualChunks(id) {
          if (id.includes("node_modules/hls.js")) return "hls"
          if (id.includes("node_modules/react-dom") || id.includes("node_modules/react/")) return "vendor"
          if (id.includes("node_modules/lucide-react")) return "icons"
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api":          "http://fleet-server:8765",
      "/ws":           { target: "ws://fleet-server:8765", ws: true },
      "/install.sh":   "http://fleet-server:8765",
      "/agent":        "http://fleet-server:8765",
    },
  },
})
