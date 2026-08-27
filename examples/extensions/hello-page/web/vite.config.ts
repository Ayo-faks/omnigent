import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  build: {
    outDir: resolve(__dirname, "../src/omnigent_hello_extension/dist"),
    emptyOutDir: true,
    lib: {
      entry: resolve(__dirname, "src/main.ts"),
      name: "OmnigentHelloExtension",
      formats: ["iife"],
      fileName: () => "extension.js",
    },
    rollupOptions: {
      output: {
        assetFileNames: (asset) =>
          asset.names.some((name) => name.endsWith(".css")) ? "extension.css" : "[name][extname]",
      },
    },
  },
});
