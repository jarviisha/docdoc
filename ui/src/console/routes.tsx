/**
 * The console's URLs: one route per area, under `/console`.
 *
 * A route only reads its params and search and hands them to the area as its
 * starting values; the area still loads its own data. The root route is the
 * frame — sidebar, top bar, notice region — around an `<Outlet />`, so a page
 * that does not exist is still shown inside it.
 *
 * Nothing comes to rest in the browser (specs/011 FR-007): scroll restoration
 * is off because it is the router's one write to `sessionStorage`, and no
 * route is lazy-loaded because `lazyRouteComponent` writes there too on a
 * failed chunk load.
 */

import { createContext, Fragment, useCallback, useContext } from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { AREA_PATHS, areaOf, type Area } from "./model/navigation.ts";
import {
  parseQuery,
  qualitySearch,
  runsSearch,
  schemaEditorSearch,
  stringifyQuery,
  tenantSearch,
} from "./model/search.ts";
import { useRequest } from "./components/RequestContext.tsx";
import { CallbacksArea } from "./components/CallbacksArea.tsx";
import { CredentialArea } from "./components/CredentialArea.tsx";
import { EraseArea } from "./components/EraseArea.tsx";
import { OverviewArea } from "./components/OverviewArea.tsx";
import { ParsersArea } from "./components/ParsersArea.tsx";
import { ProvidersArea } from "./components/ProvidersArea.tsx";
import { QualityArea } from "./components/QualityArea.tsx";
import { RunArea, RunDetail } from "./components/RunArea.tsx";
import { SchemaEditor } from "./components/SchemaEditor.tsx";
import { SchemasArea } from "./components/SchemasArea.tsx";
import { SchemaView } from "./components/SchemaView.tsx";
import { SubmitArea } from "./components/SubmitArea.tsx";
import { TenantsArea } from "./components/TenantsArea.tsx";

/** What the frame shows and does that only the signed-in `Shell` knows. */
interface Frame {
  username: string;
  notice: string | null;
  signOut: () => void;
}

export const FrameContext = createContext<Frame | null>(null);

function useFrame(): Frame {
  const frame = useContext(FrameContext);
  if (frame === null) throw new Error("useFrame outside FrameContext: only a signed-in shell renders routes");
  return frame;
}

/** A sidebar group: real areas, and the ones still to come, which go nowhere yet. */
type NavEntry = { name: Area; label: string; danger?: boolean } | { soon: string };

const NAV: { heading: string | null; entries: NavEntry[] }[] = [
  { heading: null, entries: [{ name: "overview", label: "Overview" }] },
  {
    heading: "Operate",
    entries: [
      { name: "runs", label: "Runs" },
      { name: "submit", label: "Submit" },
      { name: "quality", label: "Quality" },
    ],
  },
  { heading: "Build", entries: [{ name: "schemas", label: "Schemas" }] },
  {
    heading: "Manage",
    entries: [
      { name: "tenants", label: "Tenants" },
      { name: "credentials", label: "Credentials" },
      { name: "callbacks", label: "Callbacks" },
    ],
  },
  {
    heading: "Settings",
    entries: [
      { name: "parsers", label: "Parsers" },
      { name: "providers", label: "Providers" },
      { name: "erasure", label: "Erase…", danger: true },
    ],
  },
];

/** Each area's place in the sidebar, for the top bar's "Group / Area" trail. */
const PLACES = Object.fromEntries(
  NAV.flatMap((group) =>
    group.entries.flatMap((entry) =>
      "name" in entry ? [[entry.name, { group: group.heading, label: entry.label }]] : [],
    ),
  ),
) as Record<Area, { group: string | null; label: string }>;

function Layout() {
  const { username, notice, signOut } = useFrame();
  const area = areaOf(useLocation().pathname);
  const place = area === null ? null : PLACES[area];

  const navEntry = (entry: NavEntry) => {
    if (!("name" in entry)) {
      return (
        <span
          key={entry.soon}
          aria-disabled="true"
          className="flex items-center justify-between px-2 py-1 text-slate-400"
        >
          {entry.soon}
          <span className="text-[10px] tracking-wider uppercase">soon</span>
        </span>
      );
    }
    const selected = area === entry.name;
    return (
      <Link
        key={entry.name}
        to={AREA_PATHS[entry.name]}
        aria-current={selected ? "page" : undefined}
        className={cn(
          "rounded-sm px-2 py-1 text-left text-slate-700 outline-none hover:bg-slate-200/60 focus-visible:ring-2 focus-visible:ring-ring/50",
          selected && "bg-indigo-100 font-medium text-indigo-800 hover:bg-indigo-100",
          entry.danger === true && !selected && "text-red-600",
        )}
      >
        {entry.label}
      </Link>
    );
  };

  return (
    <div className="flex h-screen">
      <aside className="flex w-52 shrink-0 flex-col overflow-y-auto border-r bg-slate-50 px-2 py-3">
        <p className="flex items-center gap-2 px-2 pb-2 font-semibold">
          <span aria-hidden="true" className="size-3.5 rounded-[1px] bg-primary" />
          docdoc console
        </p>
        <nav aria-label="Console" className="flex flex-col gap-0.5">
          {NAV.map((group) => (
            <Fragment key={group.heading ?? "top"}>
              {group.heading === null ? null : (
                <p className="px-2 pt-4 pb-1 text-[10px] font-medium tracking-widest text-slate-500 uppercase">
                  {group.heading}
                </p>
              )}
              {group.entries.map(navEntry)}
            </Fragment>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-10 shrink-0 items-center justify-between border-b px-4">
          <span className="text-slate-500">
            {place === null ? null : (
              <>
                {place.group === null ? null : `${place.group} / `}
                <span className="font-medium text-foreground">{place.label}</span>
              </>
            )}
          </span>
          <div className="flex items-center gap-3">
            <span className="text-slate-500">Signed in as {username}</span>
            <Button variant="outline" size="sm" onClick={signOut}>
              Sign out
            </Button>
          </div>
        </header>

        <main className="flex flex-1 flex-col gap-3 overflow-auto p-4">
          {/* Empty, it would still take a gap: the negative margin gives it back. */}
          <div role="status" aria-live="polite" className="empty:-mb-3">
            {notice === null ? null : (
              <Alert variant="warning">
                <AlertTitle>{notice}</AlertTitle>
              </Alert>
            )}
          </div>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function NotFound() {
  return (
    <Alert variant="info">
      <AlertTitle>This page does not exist.</AlertTitle>
      <AlertDescription>
        <Link to="/" className="text-primary underline-offset-2 hover:underline">
          Go to Overview
        </Link>
      </AlertDescription>
    </Alert>
  );
}

/** Opening a run is a navigation, so Back returns to where it was opened from. */
function useOpenRun(): (runId: string) => void {
  const navigate = useNavigate();
  return useCallback((runId: string) => void navigate({ to: "/runs/$runId", params: { runId } }), [navigate]);
}

const rootRoute = createRootRoute({ component: Layout, notFoundComponent: NotFound });

const overviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: function Overview() {
    return <OverviewArea onOpenRun={useOpenRun()} />;
  },
});

const runsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs",
  validateSearch: runsSearch,
  component: function Runs() {
    const { tenant, status, kind } = runsRoute.useSearch();
    // Keyed by the filters: a change mounts the list afresh on page one.
    return <RunArea key={JSON.stringify([tenant, status, kind])} onOpenRun={useOpenRun()} />;
  },
});

const runRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs/$runId",
  component: function Run() {
    const { runId } = runRoute.useParams();
    const { request, requestImage } = useRequest();
    return <RunDetail key={runId} runId={runId} request={request} requestImage={requestImage} />;
  },
});

const submitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/submit",
  component: function Submit() {
    return <SubmitArea onOpenRun={useOpenRun()} />;
  },
});

const qualityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/quality",
  validateSearch: qualitySearch,
  component: function Quality() {
    return <QualityArea onOpenRun={useOpenRun()} />;
  },
});

const tenantsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tenants",
  component: function Tenants() {
    return <TenantsArea />;
  },
});

const credentialsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/credentials",
  validateSearch: tenantSearch,
  component: function Credentials() {
    return <CredentialArea />;
  },
});

const callbacksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/callbacks",
  validateSearch: tenantSearch,
  component: function Callbacks() {
    // Keyed by tenant: a write error or open confirm for one tenant never shows on another after Back.
    return <CallbacksArea key={callbacksRoute.useSearch().tenant ?? ""} />;
  },
});

const parsersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/parsers",
  component: function Parsers() {
    return <ParsersArea />;
  },
});

const providersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/providers",
  validateSearch: tenantSearch,
  component: function Providers() {
    // Keyed by scope: an open form, typed key or write error never carries to another tenant.
    return <ProvidersArea key={providersRoute.useSearch().tenant ?? ""} />;
  },
});

const schemasRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/schemas",
  component: function Schemas() {
    return <SchemasArea />;
  },
});

const schemaNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/schemas/new",
  validateSearch: schemaEditorSearch,
  component: function SchemaNew() {
    const { base } = schemaNewRoute.useSearch();
    // Keyed by the base: another base starts another draft.
    return <SchemaEditor key={base ?? ""} base={base} />;
  },
});

const schemaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/schemas/$identity",
  component: function Schema() {
    const { identity } = schemaRoute.useParams();
    return <SchemaView key={identity} identity={identity} />;
  },
});

const erasureRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/erasure",
  validateSearch: tenantSearch,
  component: function Erasure() {
    return <EraseArea />;
  },
});

const routeTree = rootRoute.addChildren([
  overviewRoute,
  runsRoute,
  runRoute,
  submitRoute,
  qualityRoute,
  tenantsRoute,
  credentialsRoute,
  callbacksRoute,
  parsersRoute,
  providersRoute,
  schemasRoute,
  schemaNewRoute,
  schemaRoute,
  erasureRoute,
]);

export const router = createRouter({
  routeTree,
  basepath: "/console",
  scrollRestoration: false,
  parseSearch: parseQuery,
  stringifySearch: stringifyQuery,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
