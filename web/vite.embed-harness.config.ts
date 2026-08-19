// Build for the embed test-harness page.
//
// Produces a tiny standalone host page (embed-harness.html + hashed JS/CSS)
// that mounts the REAL embed entry (src/embed.tsx → OmnigentApp) inside a
// minimal host shell — the same component the Databricks monolith renders,
// but bundled with its own React + react-router so it runs without the
// monolith. tests/e2e_ui/embed/ serves this output statically and drives
// embed-only behavior (e.g. the document favicon swap) in a real browser.
// Run via `pnpm run build:embed-harness`.
//
// Unlike vite.embed.config.ts (the intermediate library build that leaves
// React / react-router as bare externals for the monolith's rspack), this is
// an app build: nothing is external and the page is self-contained. Kept out
// of the main app build so it emits no service worker and never ships.

import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // Served from an ephemeral local HTTP server in tests; relative asset URLs
  // keep the page loadable from any mount path.
  base: "./",
  // The harness has no use for the web app's public/ assets (PWA icons,
  // favicon.svg) — the host page supplies its own <link rel="icon">.
  publicDir: false,
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: path.resolve(__dirname, "./dist-embed-harness"),
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(__dirname, "./embed-harness.html"),
    },
  },
});
