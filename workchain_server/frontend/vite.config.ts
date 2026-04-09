import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Serve the built assets under /designer/ so they can live behind the
// FastAPI StaticFiles mount at workchain_server/static/designer/.
export default defineConfig({
  plugins: [react()],
  base: "/designer/",
  build: {
    outDir: "../static/designer",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // During `npm run dev` the Vite dev server proxies API calls to the
      // FastAPI backend running on :8000, so the designer has hot reload
      // without CORS gymnastics.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
