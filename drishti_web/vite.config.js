import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Under vitest, Svelte's package exports resolve to its server build, whose
// mount() only renders to a string — every component test dies inside
// index-server.js. Forcing the browser condition fixes that, but the override
// must not exist outside the test run: setting resolve.conditions at all
// replaces Vite's defaults, and the production build then pulls in Svelte's
// server render context and its node:async_hooks import.
const testOnly = process.env.VITEST ? { resolve: { conditions: ["browser"] } } : {};

export default defineConfig({
  plugins: [svelte({ hot: false })],
  base: "/drishti/",
  build: { outDir: "../basic_pipelines/drishti_dist", emptyOutDir: true },
  ...testOnly,
  server: {
    // Same-origin in production; the dev server proxies so the host-scoped
    // drishti_session cookie behaves the same way while developing.
    proxy: { "/api/drishti": { target: "http://localhost:8080", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    // Component styles are the design here — a 44px hit area is a claim the
    // tests have to be able to check.
    css: true,
    globals: true,
    setupFiles: ["./tests/setup.js"],
  },
});
