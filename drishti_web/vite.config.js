import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte({ hot: false })],
  base: "/drishti/",
  build: { outDir: "../basic_pipelines/drishti_dist", emptyOutDir: true },
  server: {
    // Same-origin in production; the dev server proxies so the host-scoped
    // drishti_session cookie behaves the same way while developing.
    proxy: { "/api/drishti": { target: "http://localhost:8080", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.js"],
  },
});
