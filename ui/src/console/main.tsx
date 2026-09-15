/**
 * The console's mount point. Everything it renders decides nothing — see `Shell`.
 *
 * Served from `/console` and not from `/ui`, and that is not a naming choice:
 * the viewer's mount sits behind the API credential, and a browser's top-level
 * navigation carries no bearer token. A console gated the same way would never
 * load on the deployments it exists for (specs/011 research R1).
 */

// Astryx ships pre-built CSS — the same three sheets the viewer imports, in the
// same order, for the same reasons recorded in `src/main.tsx`.
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "@astryxdesign/theme-neutral/theme.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Shell } from "./components/Shell.tsx";

const container = document.getElementById("root");

if (container === null) {
  throw new Error("no #root element: console.html and this entry point disagree");
}

createRoot(container).render(
  <StrictMode>
    <Shell />
  </StrictMode>,
);
