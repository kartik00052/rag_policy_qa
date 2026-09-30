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
   *
   * The proxy target is NOT simply `VITE_API_URL` with the prefix stripped. Dev
   * config sets `VITE_API_URL=/api/v1` (relative, so the browser calls
   * same-origin and this proxy is what resolves it). Stripping `/api/v1` from
   * that leaves an empty string, and an empty proxy target makes every request
   * 502 - the app then shows "can't reach the policy service" while the backend
   * is running perfectly. So the target is only derived from `VITE_API_URL` when
   * it is already absolute; a relative value means "proxy me", and the concrete
   * backend origin falls back to the documented local default.
   */
  const DEFAULT_BACKEND_ORIGIN = "http://localhost:8000"
  const configured = env.VITE_API_URL ?? ""
  const isAbsolute = /^https?:\/\//i.test(configured)
  const origin = isAbsolute
    ? configured.replace(/\/api\/v1\/?$/, "")
    : DEFAULT_BACKEND_ORIGIN

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
