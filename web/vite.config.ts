import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

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
  server: {
    port: 5173,
    strictPort: true,
    // Local development against a running workbench API: set VITE_DEV_API_PORT to
    // its loopback port and the dev server forwards /v1 with the Host header the
    // authority check expects. Unset (the default) adds no proxy at all — the
    // conditional spread leaves the property out entirely, which is what
    // exactOptionalPropertyTypes requires of Vite's ServerOptions.
    ...(process.env.VITE_DEV_API_PORT
      ? {
          proxy: {
            "/v1": {
              target: `http://127.0.0.1:${process.env.VITE_DEV_API_PORT}`,
              changeOrigin: true,
            },
          },
        }
      : {}),
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
  },
});
