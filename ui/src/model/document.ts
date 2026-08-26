/**
 * What the browser can do with the bytes the user picked.
 *
 * **The viewer used to offer three media types and open one.** `Picker` declared
 * `accept="application/pdf,image/png,image/jpeg"` and every chosen file went to
 * `pdfjs.getDocument`, which opens neither image. The rejection was unhandled,
 * so picking a PNG produced no page, no message, and a run control that still
 * worked — the extraction went out, the provider was paid, and nothing appeared
 * (T092). The `accept` list now lives here, beside the code that decides what to
 * do with each type, so the offer and the capability are one fact.
 *
 * **An undrawable document is still extractable, and that is FR-058 rather than
 * generosity.** The list of values is authoritative and the overlay is an aid;
 * removing the aid removes no fact. So a TIFF — which the engine accepts and no
 * browser draws — runs, lists every value with its status and verdict, and says
 * why there is no picture. Refusing it would take away the facts as well as the
 * rectangles, which is the one thing the overlay's status as "an aid" forbids.
 *
 * Signatures mirror `src/docdoc/ingest/source.py`'s `_SIGNATURES`, deliberately
 * and not by import: this runs in a browser and that runs in Python. The set is
 * small, fixed by file formats rather than by docdoc, and the server re-detects
 * from the bytes anyway (FR-005) — nothing here is trusted, and a disagreement
 * costs a clear refusal from the deployment rather than a wrong answer.
 */

/** The media types the picker offers, as one string and one source of truth. */
export const ACCEPTED = "application/pdf,image/png,image/jpeg";

export interface DocumentView {
  /** How to draw it: `pdf` through the renderer, `image` directly, or not. */
  kind: "pdf" | "image" | "unrenderable";
  mediaType: string | null;
  /**
   * Why there is no picture, or `null` when there is one. Never a reason the
   * document was *rejected* — it was not; only the overlay is unavailable.
   */
  notice: string | null;
}

const SIGNATURES: readonly (readonly [readonly number[], string])[] = [
  [[0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], "image/png"],
  [[0x25, 0x50, 0x44, 0x46, 0x2d], "application/pdf"],
  [[0xff, 0xd8, 0xff], "image/jpeg"],
  [[0x49, 0x49, 0x2a, 0x00], "image/tiff"],
  [[0x4d, 0x4d, 0x00, 0x2a], "image/tiff"],
];

/** The media type the bytes actually are, never what the file was called. */
export function detectMediaType(bytes: Uint8Array): string | null {
  for (const [signature, mediaType] of SIGNATURES) {
    if (signature.every((byte, index) => bytes[index] === byte)) return mediaType;
  }
  return null;
}

export function toDocumentView(bytes: Uint8Array): DocumentView {
  const mediaType = detectMediaType(bytes);

  if (mediaType === "application/pdf") {
    return { kind: "pdf", mediaType, notice: null };
  }

  if (mediaType === "image/png" || mediaType === "image/jpeg") {
    // Exactly one page, and no page-splitting — the spec's Edge Cases say so,
    // and the `gcv` parser accepts these two and no PDF at all, so a deployment
    // configured with it can process nothing else.
    return { kind: "image", mediaType, notice: null };
  }

  if (mediaType !== null) {
    // **It does not say whether the deployment accepts the type**, and that is
    // the correction T097 made rather than a hedge. This branch was written
    // asserting that "docdoc accepts" TIFF and that "the extraction runs" —
    // both false. `src/docdoc/ingest/source.py` records that TIFF is *detected*
    // precisely so it can be refused as an unsupported type rather than as an
    // unrecognisable file, so the run comes back 415 and nothing is listed.
    //
    // The fix is not to name TIFF as refused here. That would put a copy of the
    // server's allowlist in the browser, and two copies of a rule are how the
    // two come to disagree — the same finding T085 made about the asset path.
    // What this page knows is that it cannot draw the type; what the deployment
    // accepts is the deployment's answer, and it already gives it with a typed
    // reason (FR-027).
    return {
      kind: "unrenderable",
      mediaType,
      notice:
        `This is ${mediaType}, which no browser draws, so there is no page image here. ` +
        "If the deployment accepts the type, every value is still listed below with its " +
        "grounding status — the list is authoritative and the overlay only an aid. " +
        "If it does not, its refusal will say so.",
    };
  }

  return {
    kind: "unrenderable",
    mediaType: null,
    notice:
      "These bytes match no format this viewer recognises. The extraction is " +
      "still sent, and the deployment will refuse it with a typed reason if it " +
      "does not accept the type either; nothing is drawn meanwhile.",
  };
}

/**
 * How many pages the browser will draw before the result names any.
 *
 * One for an image, and for anything else the answer comes from the renderer or
 * is zero. Stated here rather than inferred at a call site so `pageCount` has a
 * single origin — `toRunView` uses it to say what it is *not* showing (FR-054),
 * and a wrong count there is a false statement about the document.
 */
export function pageCountFor(view: DocumentView, fromRenderer: number | null): number {
  if (view.kind === "image") return 1;
  return fromRenderer ?? 0;
}

/** Why the picture failed when the renderer itself refused the bytes. */
export function renderFailureNotice(mediaType: string | null): string {
  const what = mediaType === null ? "This document" : `This ${mediaType}`;
  return (
    `${what} could not be opened for display. The extraction can still run and ` +
    "its values will be listed; only the page image is unavailable."
  );
}
