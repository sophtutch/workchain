import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// SPA served at / — FastAPI mounts the built output from static/app/.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../static/app",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // During `npm run dev` the Vite dev server proxies API calls to the
      // FastAPI backend running on :8000, so the app has hot reload
      // without CORS gymnastics.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
      "/static": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
