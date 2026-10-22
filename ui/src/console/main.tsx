/**
 * The console's mount point. Everything it renders decides nothing — see `Shell`.
 *
 * Served from `/console` and never gated by the API credential: a browser's
 * top-level navigation carries no bearer token, so a gated shell would never
 * load on the deployments this exists for (specs/011 research R1). It stayed at
 * `/console` rather than moving to `/ui` when the viewer was removed
 * (ADR-0020) — the path is in operators' bookmarks and in FR-001, and neither is
 * worth breaking to reclaim a shorter prefix.
 */

// Astryx ships pre-built CSS. Three sheets, in this order: the reset first, the
// component styles on top of it, the theme's custom properties last, because the
// theme overrides tokens the other two declare.
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "@astryxdesign/theme-neutral/theme.css";
import "../styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";

import { Shell } from "./components/Shell.tsx";

const container = document.getElementById("root");

if (container === null) {
  throw new Error("no #root element: index.html and this entry point disagree");
}

createRoot(container).render(
  <StrictMode>
    {/* The sheets above declare the tokens; `Theme` is what applies the body
        font and text colour to the tree. Without it everything fell back to the
        browser's serif default. */}
    <Theme theme={neutralTheme}>
      <Shell />
    </Theme>
  </StrictMode>,
);
