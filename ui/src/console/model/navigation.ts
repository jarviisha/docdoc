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
  | "erasure"
  | "quality";

/** One tenant row's jump: which area to show, and which tenant to show it for. */
export interface Jump {
  area: Area;
  tenant: string | null;
}

/** A tenant row's own jump into one of its areas. */
export function jumpTo(area: Area, tenant: string): Jump {
  return { area, tenant };
}

/** The tenants screen's own shortcuts, in the order they are offered. */
export const TENANT_JUMPS: { area: Area; label: string }[] = [
  { area: "runs", label: "Runs" },
  { area: "credentials", label: "Credentials" },
  { area: "callbacks", label: "Callbacks" },
  { area: "erasure", label: "Erase…" },
];
