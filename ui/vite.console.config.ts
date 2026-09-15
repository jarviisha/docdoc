import { copyFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

// A second config rather than a second entry in the first one, and the reason is
// not cosmetic: Vite's `base` is per-build, and research R1 put the console on a
// path the viewer's base cannot reach. Multi-page input under `base: "/ui/"`
// would work mechanically and would serve the console from behind the viewer's
// credential-gated mount — where it would never load, because a browser's
// top-level navigation carries no bearer token.
export default defineConfig({
  base: "/console/",

  build: {
    // `docdoc.api.ui` looks here, and the `docdoc-ui` distribution ships it
    // beside `dist`. Never committed.
    outDir: "dist-console",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: fileURLToPath(new URL("./console.html", import.meta.url)),
    },
  },

  plugins: [
    {
      // Vite writes the entry under the name it was given, so this build emits
      // `console.html`. `StaticFiles(html=True)` on the Python side looks for
      // `index.html` and nothing else, so `/console/` would answer 404 at its
      // own root — a blank door, which is the failure `specs/008` FR-037 spent
      // a requirement on.
      //
      // Copying is the smaller fix than either alternative: renaming the source
      // entry to `index.html` collides with the viewer's in the same directory,
      // and teaching the mount a second entry name means re-implementing static
      // file serving in Python. `console.html` stays as the marker that tells a
      // console build from any other `dist`.
      name: "console-entry-as-index",
      writeBundle(options) {
        const directory = options.dir ?? "dist-console";
        copyFileSync(join(directory, "console.html"), join(directory, "index.html"));
      },
    },
  ],

  resolve: {
    alias: {
      "@model": fileURLToPath(new URL("./src/model", import.meta.url)),
      "@components": fileURLToPath(new URL("./src/components", import.meta.url)),
      "@console": fileURLToPath(new URL("./src/console", import.meta.url)),
    },
  },

  server: {
    // Development only, and the same reason the viewer's config gives: there is
    // no mock, so `vite dev` talks to a real deployment.
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
