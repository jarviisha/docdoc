import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

// One build, one config. There were two until the viewer was removed
// (ADR-0020): Vite's `base` is per-build, the two surfaces were served from
// different paths, and a multi-page input would have put the console behind the
// viewer's credential-gated mount — where it would never load, because a
// browser's top-level navigation carries no bearer token.
//
// Astryx ships pre-built CSS and JS, so there is no StyleX plugin, no Babel
// step, and no PostCSS configuration here (specs/008 research R2). If a future
// change needs one, that is a finding about Astryx rather than a gap in this
// file.
export default defineConfig({
  // Served from the same origin as the API, under this prefix, so no
  // cross-origin configuration exists anywhere (specs/008 FR-034).
  base: "/console/",

  build: {
    // `docdoc.api.ui` looks here, and `packaging/docdoc-ui/build.sh` packages
    // it. Never committed (specs/008 FR-038).
    //
    // `dist-console` until the viewer went: it was named for the `dist` it sat
    // beside, and with nothing beside it the qualifier described a distinction
    // that no longer exists.
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },

  // No `rollupOptions.input` and no entry-copying plugin. The entry is
  // `index.html`, which is the name `StaticFiles(html=True)` looks for on the
  // Python side — it was `console.html` only because it shared a directory with
  // the viewer's `index.html` and could not have the name it wanted.

  resolve: {
    alias: {
      "@console": fileURLToPath(new URL("./src/console", import.meta.url)),
    },
  },

  server: {
    // Development only. The built assets are served by the API itself, so this
    // proxy exists so that `vite dev` talks to a real deployment rather than a
    // mock — there is no mock, and a console tested against one would be
    // testing the mock.
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
