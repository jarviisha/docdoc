/**
 * The shapes the view model produces, and the wire shapes it consumes.
 *
 * Two vocabularies meet here and are kept apart on purpose. The `Wire*` types
 * describe what `POST /v1/extract` actually returns — verified against a real
 * response, not inferred from the contract — and the rest describe what a
 * renderer is given. Nothing outside this directory should ever see a `Wire*`
 * type: the moment a component reads `geometry === null` it has taken over a
 * decision the model owes it (FR-041, FR-043).
 */

// -- the wire ---------------------------------------------------------------

/** `[page_index, [x0, y0, x1, y1]]` — a NamedTuple, serialised as a pair. */
export type WireGeometry = [number, [number, number, number, number]];

export interface WireOutcome {
  field_path: string;
  status: "exact" | "fuzzy" | "ungrounded";
  score: number | null;
  span: [number, number] | null;
  pages: number[];
  /**
   * Three states, never two (FR-018).
   *
   * `null` on a *grounded* value means the parser supplied no geometry; `[]`
   * means geometry exists and this range covers no tokens; a non-empty array
   * means boxes. On an ungrounded value `null` means only that there is nothing
   * to locate, which is a fourth situation and not one of the three.
   */
  geometry: WireGeometry[] | null;
}

export interface WireValue {
  field_path: string;
  value: unknown;
  /** `false` when the model reported the field absent — it then has no outcome. */
  present: boolean;
  claimed_text: string | null;
}

export interface WireRun {
  document_id: string | null;
  schema_identity: string;
  verdict: string | null;
  extraction: { values: Record<string, unknown> } | null;
  grounding: { outcomes: Record<string, WireOutcome> } | null;
  validation: { verdict: string; findings: WireFinding[] } | null;
}

export interface WireFinding {
  field_path: string | null;
  severity: string;
  reason: string;
  message?: string | null;
}

// -- what a renderer is given ------------------------------------------------

export type GroundingStatus = "exact" | "fuzzy" | "ungrounded";

/**
 * Where a value sits, in the three states the engine keeps apart.
 *
 * A discriminated union rather than a nullable array, so that collapsing
 * "the parser gave no geometry" into "there is nothing there" requires writing
 * the collapse rather than forgetting the difference.
 */
export type GeometryState =
  | { kind: "unavailable" }
  | { kind: "empty" }
  | { kind: "located"; boxes: BoxEntry[] }
  | { kind: "not-applicable" };

export interface BoxEntry {
  pageIndex: number;
  /** Normalized 0..1, top-left origin, **as received** — see `run.ts`. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  fieldPath: string;
}

/** A score never travels without the tier it belongs to (FR-020, ADR-0004). */
export interface ScoreView {
  value: number;
  tier: "exact" | "fuzzy";
}

export interface ValueRow {
  fieldPath: string;
  value: string | null;
  presence: "asserted" | "absent";
  verdict: string;
  status: GroundingStatus | null;
  score: ScoreView | null;
  geometry: GeometryState;
  pages: number[];
  boxes: BoxEntry[];
  /** Textual equivalents, so no distinction can depend on colour (FR-057). */
  labels: { status: string; verdict: string; geometry: string };
}

export interface RunView {
  values: ValueRow[];
  boxesByPage: Map<number, BoxEntry[]>;
  pagesToRender: number[];
  pageCount: number;
  selection: string | null;
}
