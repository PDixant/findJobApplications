import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes the app into the Python package, where app.py serves it.
// `npm run dev` proxies /api to `python app.py --dev` on port 8765.
export default defineConfig({
  plugins: [react()],
  // Fixed names for the app's own files, so a rebuild replaces them instead of adding new ones.
  build: {
    outDir: "../jobwatcher/ui_dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: (info) => (info.names?.some((n) => n.endsWith(".css")) ? "assets/app.css" : "assets/[name]-[hash][extname]"),
      },
    },
  },
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8765" } } },
});
