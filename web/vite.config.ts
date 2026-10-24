import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Served by FastAPI at /app/ (see server/dashboard.py). In dev, /api is proxied to the server.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "node" },
});
