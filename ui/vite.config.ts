import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

// Astryx ships pre-built CSS and JS, so there is no StyleX plugin, no Babel
// step, and no PostCSS configuration here (research R2). If a future change
// needs one, that is a finding about Astryx rather than a gap in this file.
export default defineConfig({
  // The application is served from the same origin as the API, under this
  // prefix, so no cross-origin configuration exists anywhere (FR-034).
  base: "/ui/",

  build: {
    // `docdoc.api.ui` looks here, and the packaging of T066 builds the
    // `docdoc-ui` distribution from it. Never committed (FR-038).
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },

  resolve: {
    alias: {
      "@model": fileURLToPath(new URL("./src/model", import.meta.url)),
      "@components": fileURLToPath(new URL("./src/components", import.meta.url)),
    },
  },

  server: {
    // Development only. The built assets are served by the API itself, so this
    // proxy exists so that `vite dev` talks to a real deployment rather than a
    // mock -- there is no mock, and a viewer tested against one would be
    // testing the mock.
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
