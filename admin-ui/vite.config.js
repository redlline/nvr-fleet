import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
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
