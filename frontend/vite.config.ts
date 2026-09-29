import path from "path"
import { defineConfig, loadEnv } from "vite"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname, "VITE_")

  /*
   * Dev-only proxy to the backend.
   *
   * The backend serves no CORS headers at all - it registers no CORS
   * middleware - so a browser on :5173 cannot call :8000 directly. Every real
   * request fails the preflight with "No 'Access-Control-Allow-Origin' header",
   * which surfaces to the user as an opaque "Failed to fetch" rather than as a
   * diagnosable error. Proxying in dev keeps the browser same-origin.
   *
   * Deliberately not used in production builds: `VITE_API_URL` stays absolute
   * there, and a deployed frontend is expected to be served behind whatever
   * reverse proxy owns that origin.
   */
  const apiTarget = env.VITE_API_URL || "http://localhost:8000/api/v1"
  const origin = apiTarget.replace(/\/api\/v1\/?$/, "")

  return {
    plugins: [tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    server: {
      proxy: {
        "/api/v1": {
          target: origin,
          changeOrigin: true,
        },
      },
    },
  }
})
