/**
 * What the browser can draw, and what it must not silently refuse.
 *
 * T092: the picker offered three media types and the code opened one. These
 * assert the offer and the capability are now the same fact, and that the two
 * types pdf.js cannot open reach a renderer that can rather than a rejection
 * nobody handles.
 *
 * The FR-058 half matters as much as the drawing half: a document the browser
 * cannot draw is still extracted and still listed. The overlay is an aid, and
 * removing an aid must remove no fact.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  ACCEPTED,
  detectMediaType,
  pageCountFor,
  renderFailureNotice,
  toDocumentView,
} from "../src/model/document.ts";

const bytesOf = (...prefix: number[]) => new Uint8Array([...prefix, 0, 1, 2, 3]);

const PDF = bytesOf(0x25, 0x50, 0x44, 0x46, 0x2d);
const PNG = bytesOf(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a);
const JPEG = bytesOf(0xff, 0xd8, 0xff);
const TIFF = bytesOf(0x49, 0x49, 0x2a, 0x00);
const NONSENSE = bytesOf(0x00, 0x00, 0x00, 0x00);

describe("the type is read from the bytes", () => {
  it("recognises every signature the engine does", () => {
    assert.equal(detectMediaType(PDF), "application/pdf");
    assert.equal(detectMediaType(PNG), "image/png");
    assert.equal(detectMediaType(JPEG), "image/jpeg");
    assert.equal(detectMediaType(TIFF), "image/tiff");
  });

  it("returns null rather than guessing", () => {
    assert.equal(detectMediaType(NONSENSE), null);
  });

  it("does not match a signature that merely appears later in the file", () => {
    const buried = new Uint8Array([0x00, 0x25, 0x50, 0x44, 0x46, 0x2d]);

    assert.equal(detectMediaType(buried), null);
  });
});

describe("everything the picker offers can be drawn", () => {
  it("offers exactly the types with a rendering path", () => {
    // The regression T092 records: `accept` listed PNG and JPEG, and every file
    // went to pdf.js, which opens neither. If a type is added to one of these
    // lists and not the other, this fails.
    const offered = ACCEPTED.split(",");
    const drawable = [PDF, PNG, JPEG].map((bytes) => toDocumentView(bytes).mediaType);

    assert.deepEqual([...offered].sort(), [...drawable].sort());
    for (const bytes of [PDF, PNG, JPEG]) {
      assert.notEqual(toDocumentView(bytes).kind, "unrenderable");
    }
  });

  it("sends a PDF to the renderer and an image straight to the page", () => {
    assert.equal(toDocumentView(PDF).kind, "pdf");
    assert.equal(toDocumentView(PNG).kind, "image");
    assert.equal(toDocumentView(JPEG).kind, "image");
  });

  it("gives an image exactly one page, with no renderer to ask", () => {
    // Spec §Edge Cases: such a document has one page and needs no splitting.
    assert.equal(pageCountFor(toDocumentView(PNG), null), 1);
  });

  it("takes the page count from the renderer for a PDF", () => {
    assert.equal(pageCountFor(toDocumentView(PDF), 12), 12);
    assert.equal(pageCountFor(toDocumentView(PDF), null), 0);
  });
});

describe("a document the browser cannot draw", () => {
  it("loses the picture without claiming to lose the facts (FR-058)", () => {
    const view = toDocumentView(TIFF);

    assert.equal(view.kind, "unrenderable");
    assert.ok(view.notice);
    assert.match(view.notice ?? "", /listed|values/i);
  });

  it("does not claim the deployment accepts the type", () => {
    // T097. This branch used to say "which docdoc accepts and no browser draws"
    // and "the extraction runs" — both false for the one type that reaches it.
    // `src/docdoc/ingest/source.py` detects TIFF *so that it can be refused*, so
    // the run returns 415 and nothing is listed.
    //
    // The assertion is deliberately about what the notice must NOT promise
    // rather than about TIFF being refused: naming the allowlist here would put
    // a second copy of it in the browser, which is the defect T085 fixed
    // elsewhere. The deployment answers that question with a typed reason.
    const notice = toDocumentView(TIFF).notice ?? "";

    assert.doesNotMatch(notice, /docdoc accepts|which docdoc/i);
    assert.doesNotMatch(
      notice,
      /the extraction runs\b/i,
      "the notice may not promise a run the deployment has not agreed to",
    );
    assert.match(notice, /if the deployment accepts/i);
  });

  it("names the type when it knows it", () => {
    assert.match(toDocumentView(TIFF).notice ?? "", /image\/tiff/);
  });

  it("says the deployment decides when the type is unrecognised", () => {
    const view = toDocumentView(NONSENSE);

    assert.equal(view.kind, "unrenderable");
    assert.equal(view.mediaType, null);
    assert.match(view.notice ?? "", /deployment/);
  });

  it("explains a renderer that refused bytes claiming to be a PDF", () => {
    const notice = renderFailureNotice("application/pdf");

    assert.match(notice, /application\/pdf/);
    assert.match(notice, /still run|can still/i);
  });
});
