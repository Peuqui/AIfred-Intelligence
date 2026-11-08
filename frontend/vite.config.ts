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
          // A group takes only what its test names: by default it pulled in Vue itself (imported by VueFinder), and
          // the chat page loaded the requester's chunk at once.
          includeDependenciesRecursively: false,
          groups: [
            { name: "katex", test: /node_modules[\\/]katex/ },
            { name: "highlight", test: /node_modules[\\/]highlight\.js/ },
            // The file requester's VueFinder, loaded only when a requester opens, in three parts below the size limit: its
            // text preview's CodeMirror (VueFinder imports it when a text is shown), the libraries it ships bundled in
            // dist/, and VueFinder itself with the packages it imports.
            { name: "codemirror", test: /node_modules[\\/]vuefinder[\\/]dist[\\/]CodeMirrorEditor/ },
            { name: "finder-libs", test: /node_modules[\\/]vuefinder[\\/]dist[\\/](dist-|papaparse)/ },
            {
              name: "finder",
              test: /node_modules[\\/](vuefinder|@uppy|vue-advanced-cropper|@viselect|vanilla-lazyload|overlayscrollbars|nanostores|@nanostores|vue-sonner|@floating-ui|@tanstack)[\\/]/,
            },
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
