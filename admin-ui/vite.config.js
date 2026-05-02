import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Split hls.js into its own chunk — it is ~500 kB and only needed
        // on WatchPage / ArchiveTab. This eliminates the Vite chunk size warning
        // and improves initial load time for users who never open a stream.
        manualChunks: {
          "hls": ["hls.js"],
          "vendor": ["react", "react-dom"],
          "icons": ["lucide-react"],
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
