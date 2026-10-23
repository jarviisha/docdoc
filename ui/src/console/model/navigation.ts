/**
 * The console's areas, and a tenant's own jump into one of them.
 *
 * `Area` used to be `Shell.tsx`'s own type, naming only the five it rendered.
 * It moves here because the tenants screen (plan 4) needs the full set to
 * jump into — runs, credentials, callbacks, and erasure, each scoped to one
 * tenant — and a navigation decision belongs in the model for the same
 * reason every other decision does: `check-model-boundary.mjs` makes the
 * model the only tested surface, and a jump target chosen in a component
 * would be a requirement with no coverage.
 */

export type Area =
  | "overview"
  | "runs"
  | "submit"
  | "parsers"
  | "tenants"
  | "credentials"
  | "callbacks"
  | "schemas"
  | "providers"
  | "erasure"
  | "quality";

export type AreaPath =
  | "/"
  | "/runs"
  | "/submit"
  | "/quality"
  | "/tenants"
  | "/credentials"
  | "/callbacks"
  | "/schemas"
  | "/providers"
  | "/parsers"
  | "/erasure";

/** Each area's route path, relative to the `/console` base. */
export const AREA_PATHS: { readonly [A in Area]: AreaPath } = {
  overview: "/",
  runs: "/runs",
  submit: "/submit",
  quality: "/quality",
  tenants: "/tenants",
  credentials: "/credentials",
  callbacks: "/callbacks",
  schemas: "/schemas",
  providers: "/providers",
  parsers: "/parsers",
  erasure: "/erasure",
};

/**
 * The area a pathname belongs to, with or without the `/console` prefix and a
 * trailing slash; nested paths (`/runs/abc`) belong to their first segment.
 */
export function areaOf(pathname: string): Area | null {
  const rest = pathname.replace(/^\/console(?=\/|$)/, "");
  const first = rest.split("/")[1] ?? "";
  for (const [area, path] of Object.entries(AREA_PATHS)) {
    if (path === `/${first}`) return area as Area;
  }
  return null;
}

/** One tenant row's jump: the area's path, and the tenant to show it for. */
export interface Jump {
  to: AreaPath;
  search: { tenant: string };
}

/** A tenant row's own jump into one of its areas. */
export function jumpTo(area: Area, tenant: string): Jump {
  return { to: AREA_PATHS[area], search: { tenant } };
}

/** The tenants screen's own shortcuts, in the order they are offered. */
export const TENANT_JUMPS: { area: Area; label: string }[] = [
  { area: "runs", label: "Runs" },
  { area: "credentials", label: "Credentials" },
  { area: "callbacks", label: "Callbacks" },
  { area: "erasure", label: "Erase…" },
];
