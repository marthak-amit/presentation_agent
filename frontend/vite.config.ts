import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/decks": backend,
      "/media": backend,
      "/stock": backend,
      "/sessions": backend,
      "/logs": backend,
      "/preflight": backend,
      "/health": backend,
      "/ws": { target: backend, ws: true },
    },
  },
});
