import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes the app into the Python package, where app.py serves it.
// `npm run dev` proxies /api to `python app.py --dev` on port 8765.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../jobwatcher/ui_dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8765" } } },
});
