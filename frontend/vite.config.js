import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Backend origin and dev-server port are env-driven so a blocked/occupied port
// (common on Windows, where 8000 can fall inside a reserved range) can be
// changed without editing source. Set VITE_API_TARGET / VITE_DEV_PORT in
// frontend/.env or the shell.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      port: Number(env.VITE_DEV_PORT) || 5173,
      proxy: {
        "/api": {
          target: env.VITE_API_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
  };
});
