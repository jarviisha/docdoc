/**
 * A run's OCR result, read from the `Document` body `GET …/result` answers
 * with, and grouped by page for the split view `run-page`'s image sits
 * beside.
 *
 * **Code points, not UTF-16 units.** `text[s:e]` in the contract means the
 * canonical string's own Unicode code points — a character outside the
 * Basic Multilingual Plane (an emoji, some CJK) is two UTF-16 units, and
 * slicing a JavaScript string with `.slice(s, e)` would cut such a token a
 * unit short of where the API meant. `Array.from(text)` splits by code
 * point once, and every token slices that array instead.
 *
 * **A token's page.** `tokens[]` names its page directly in `geometry[0]`
 * when it has geometry; a token the OCR host returned no box for carries no
 * page either, so it is placed by which page's `span` contains its start —
 * the same span `pages[]` already states for exactly this.
 */

/** One token, placed on its page with the box (if any) and its own index. */
export interface ResultLine {
  text: string;
  bbox: [number, number, number, number] | null;
  confidence: number | null;
  /** The token's position in `tokens[]` — how the two panes pair a hover. */
  index: number;
}

export interface ResultPage {
  index: number;
  width: number;
  height: number;
  lines: ResultLine[];
}

interface DocumentPage {
  index: number;
  span: [number, number];
  width: number;
  height: number;
  rotation: number;
}

type TokenGeometry = [number, [number, number, number, number]] | null;
type Token = [[number, number], TokenGeometry, number | null];

/** The page whose `span` contains `start`, or `null` if none does. */
function pageForSpan(pages: DocumentPage[], start: number): number | null {
  for (const candidate of pages) {
    const [lo, hi] = candidate.span;
    if (start >= lo && start < hi) return candidate.index;
  }
  return null;
}

/**
 * Read a `Document` body into its pages. Throws on a body that is not one —
 * missing `text`, `pages`, or `tokens` is not a document with nothing in it,
 * it is a response this console failed to parse (as `toOverview` already
 * treats a missing `counts`).
 */
export function toResultPages(document: unknown): ResultPage[] {
  if (typeof document !== "object" || document === null) {
    throw new Error("the result body was not an object");
  }
  const record = document as { text?: unknown; pages?: unknown; tokens?: unknown };
  if (
    typeof record.text !== "string" ||
    !Array.isArray(record.pages) ||
    !Array.isArray(record.tokens)
  ) {
    throw new Error("the result body carried no text, pages, and tokens arrays");
  }

  const codePoints = Array.from(record.text);
  const pagesInput = record.pages as DocumentPage[];
  const tokens = record.tokens as Token[];

  const pages = new Map<number, ResultPage>();
  for (const page of pagesInput) {
    pages.set(page.index, { index: page.index, width: page.width, height: page.height, lines: [] });
  }

  tokens.forEach((token, tokenIndex) => {
    const [[start, end], geometry, confidence] = token;
    const pageIndex = geometry !== null ? geometry[0] : pageForSpan(pagesInput, start);
    const page = pageIndex === null ? undefined : pages.get(pageIndex);
    if (page === undefined) return; // no page claims this token
    page.lines.push({
      text: codePoints.slice(start, end).join(""),
      bbox: geometry !== null ? geometry[1] : null,
      confidence,
      index: tokenIndex,
    });
  });

  return pagesInput.map((page) => pages.get(page.index)).filter((page): page is ResultPage => page !== undefined);
}

/**
 * Whether the OCR host's own page dimensions disagree with the rendered
 * image's, by orientation — a document the host read sideways. Dimensions
 * within 2 % of square are left `false`: an aspect ratio that close to 1:1
 * swaps "landscape" for "portrait" on noise alone, not on a real turn.
 */
function nearSquare(width: number, height: number): boolean {
  return Math.abs(width - height) / Math.max(width, height) < 0.02;
}

export function isTurned(page: ResultPage, imageWidth: number, imageHeight: number): boolean {
  if (nearSquare(page.width, page.height) || nearSquare(imageWidth, imageHeight)) return false;
  return page.width > page.height !== imageWidth > imageHeight;
}

/** Fixed copy for `isTurned`'s page, so boxes that no longer line up say why. */
export const TURNED_NOTICE = "This page was turned by the OCR host; boxes may not line up.";

/** A token's confidence, as a percentage: `"—"` for `null`, else rounded. */
export function confidenceLabel(c: number | null): string {
  if (c === null) return "—";
  return `${Math.round(c * 100)} %`;
}

/** The page `step` away from `index` among `count`, or `null` past either end. */
export function pageAfter(index: number, count: number, step: -1 | 1): number | null {
  const next = index + step;
  return next >= 0 && next < count ? next : null;
}
