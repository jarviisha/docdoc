/**
 * The mount point. Everything it renders decides nothing — see `App`.
 */

// Astryx ships pre-built CSS — no StyleX plugin, no PostCSS (research R2).
// `reset.css` first, then the design system's own sheet, which is the order
// its documentation specifies and the order a cascade needs.
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
// The **stylesheet**, not the package. `@astryxdesign/theme-neutral` declares
// `"sideEffects": false` and its entry point exports JavaScript, so the bare
// `import "@astryxdesign/theme-neutral"` this used to be was tree-shaken away
// entirely and the theme was never applied — measured, not suspected: zero of
// the ten custom properties unique to `theme.css` reached the built stylesheet.
//
// Nothing caught it. The build succeeded, types checked, the guards passed and
// the page rendered, because `astryx.css` alone supplies the base appearance.
// The theme's absence showed only as colours that were never overridden (T087).
import "@astryxdesign/theme-neutral/theme.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./components/App.tsx";

const container = document.getElementById("root");

if (container === null) {
  throw new Error("no #root element: index.html and this entry point disagree");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
