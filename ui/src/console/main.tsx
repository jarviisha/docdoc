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

// Tailwind's preflight, the theme tokens, and the indigo accent (ADR-0024).
import "../styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Shell } from "./components/Shell.tsx";

/**
 * The one cache of server answers, in memory only: no persister, so nothing
 * comes to rest in the browser (specs/011 FR-007). A failure is shown, not
 * retried behind the operator's back, and a hidden tab does not poll.
 */
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchIntervalInBackground: false } },
});

const container = document.getElementById("root");

if (container === null) {
  throw new Error("no #root element: index.html and this entry point disagree");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  </StrictMode>,
);
