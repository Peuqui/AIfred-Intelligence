import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

// Relative asset paths: the same build runs under /api/app/ (beside Reflex) and later /aifred/.
// The dev server forwards the API to the running backend.
export default defineConfig({
  base: "./",
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // KaTeX alone is about 520 kB minified and cannot be split further; it sits in its own chunk.
    chunkSizeWarningLimit: 600,
    // The big libraries in chunks of their own: the app's code changes, they do not.
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "katex", test: /node_modules[\\/]katex/ },
            { name: "highlight", test: /node_modules[\\/]highlight\.js/ },
            { name: "vendor", test: /node_modules/ },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8002", changeOrigin: false },
      "/_upload": { target: "http://127.0.0.1:8002", changeOrigin: false },
    },
  },
  test: { environment: "jsdom" },
});
