import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    // #25 requires that the build artifact carry no sensitive data. A source map would
    // ship the original sources, so it is off for the artifact that actually gets served.
    sourcemap: false,
    outDir: "dist",
  },
  server: { port: 5173, strictPort: true },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
  },
});
