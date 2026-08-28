/**
 * The viewer, wired together. It decides nothing.
 *
 * Every branch here is either "which component renders" or "hand this event to
 * the model" — no conditional in this file changes which value, box, page,
 * state or message a user sees. That is FR-043, and it is the requirement that
 * decides whether the untested rendering layer stays small or swallows the
 * feature: a decision that drifts in here has no coverage at all.
 *
 * Three of this file's four convergence findings were about facts it held and
 * never used, which is the shape a decision-free component fails in. It kept a
 * `pdf` it never cleared, so a second document that would not open left the
 * first one's pages under the new run's rectangles (T093). It sent every file
 * to pdf.js although the picker offered two types pdf.js cannot open (T092). It
 * read `state.kind === "complete"`, so a mid-run failure's surviving values —
 * present in the response, listed by the model — could not reach the screen
 * (T091). And it never called `pagesForSelection`, so selecting a field lit its
 * rectangles without going to them (T094).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Banner, Heading, Stack, Text } from "@astryxdesign/core";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";

import {
  isReadyToRun,
  openingNotice,
  pageCountFor,
  renderFailureNotice,
  toDocumentView,
  type DocumentView,
} from "../model/document.ts";
import { failureNotice, failureTitle, toFailureView, transportFailure } from "../model/failure.ts";
import {
  reachablePages,
  rendered,
  requestPage,
  requestsForResult,
  selectivityNotice,
  type PageRequests,
} from "../model/pages.ts";
import { requestFor } from "../model/client.ts";
import { send } from "../transport.ts";
import { pagesForSelection, select, toRunView } from "../model/run.ts";
import type { WireRun } from "../model/types.ts";
import { emptyRegistryNotice, toSchemaChoices, type SchemaChoice } from "../model/schemas.ts";
import {
  canStartRun,
  failureOf,
  initial,
  reduce,
  resultIdOf,
  viewOf,
  waitingLabel,
  type RunState,
} from "../model/state.ts";
import { ImagePage } from "./ImagePage.tsx";
import { Overlay } from "./Overlay.tsx";
import { Page } from "./Page.tsx";
import { PageNav } from "./PageNav.tsx";
import { Picker } from "./Picker.tsx";
import { Running } from "./Running.tsx";
import { ValueList } from "./ValueList.tsx";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const PAGE_WIDTH = 720;

export function App() {
  // `null` until the listing arrives: "not yet known" is not "known to be
  // none", and conflating them made the interface assert on first paint that
  // the deployment had no schemas configured (T080).
  const [schemas, setSchemas] = useState<SchemaChoice[] | null>(null);
  const [schema, setSchema] = useState<string | null>(null);
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [doc, setDoc] = useState<DocumentView | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [state, setState] = useState<RunState>(initial);
  // `requests` is the only per-run state this component still holds of its own.
  // It is rebuilt from the model's answer when a *result* arrives (T103, T105),
  // never from a response — so a discarded run cannot reach it either.
  const [requests, setRequests] = useState<PageRequests | null>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  /** The selection we have already scrolled to, so we do it once. */
  const scrolledFor = useRef<string | null>(null);

  useEffect(() => {
    void send(requestFor({ type: "list-schemas" }))
      .then(({ body }) => setSchemas(toSchemaChoices(body as Parameters<typeof toSchemaChoices>[0])))
      .catch(() => setSchemas([]));
  }, []);

  const onDocument = useCallback(async (file: File) => {
    const buffer = new Uint8Array(await file.arrayBuffer());
    const view = toDocumentView(buffer);

    setBytes(buffer);
    setDoc(view);
    // The model clears the previous result before any new box can exist, and
    // invalidates whatever is in flight (FR-028).
    setState((previous) => reduce(previous, { type: "document-chosen" }));
    setRequests(null);
    pageRefs.current.clear();
    scrolledFor.current = null;
    // **Cleared before the new one is opened, never after.** Leaving the old
    // document mounted while the new one loads is how a rectangle from one run
    // ends up over another run's page — the failure the spec calls the worst
    // this feature has, because it renders without error (T093, FR-028).
    setPdf(null);

    if (view.kind !== "pdf") return;

    try {
      setPdf(await pdfjs.getDocument({ data: buffer.slice() }).promise);
    } catch {
      // The renderer refused bytes that claimed to be a PDF. The extraction is
      // still worth running — the list is authoritative and the overlay is an
      // aid (FR-058) — so this reports the missing picture and nothing else.
      setDoc({ ...view, kind: "unrenderable", notice: renderFailureNotice(view.mediaType) });
    }
  }, []);

  /**
   * Start a run.
   *
   * **Every outcome goes to exactly one place: `reduce`.** Nothing here writes a
   * banner, a page set, or any other copy of the result, because a second copy
   * is a copy the run token does not guard — which is how a run the model had
   * already discarded still put "The run failed" on screen (T103). What a
   * failure *says* is `failure.ts`'s answer, and which run is current is
   * `state.ts`'s; this only carries the response between them.
   */
  const onRun = useCallback(() => {
    if (bytes === null || schema === null) return;
    const token = Date.now();
    setState((previous) => reduce(previous, { type: "run-started", token }));

    const pageCount = pageCountFor(doc ?? toDocumentView(bytes), pdf?.numPages ?? null);
    const plan = requestFor({ type: "extract", schema, document: bytes });

    void send(plan, bytes.slice())
      .then(({ ok, body }) => {
        // The surviving stages are read out of the same body (FR-025), so a
        // mid-run failure shows what it produced instead of only saying that it
        // produced something.
        const event = ok
          ? ({ type: "result", token, view: toRunView(body as WireRun, pageCount) } as const)
          : ({
              type: "failure",
              token,
              failure: toFailureView(body as Parameters<typeof toFailureView>[0], pageCount),
            } as const);

        setState((previous) => reduce(previous, event));
      })
      .catch((error: unknown) => {
        // The connection failed; the run did not. Saying otherwise is what the
        // spec's Edge Case forbids, and the sentence that says it correctly is
        // the model's (T102).
        setState((previous) =>
          reduce(previous, { type: "failure", token, failure: transportFailure(error) }),
        );
      });
  }, [bytes, schema, pdf, doc]);

  useEffect(() => {
    if (state.kind !== "running") return;
    const started = Date.now();
    // Elapsed time is measured here and handed to the model, which reads no
    // clock of its own — that is what keeps it pure and its tests instant.
    const timer = setInterval(
      () => setState((previous) => reduce(previous, { type: "tick", elapsedMs: Date.now() - started })),
      1000,
    );
    return () => clearInterval(timer);
  }, [state.kind]);

  // A completed run, or a failed one's survivors. Which is which is the model's
  // answer, not this file's (FR-025, T091).
  const view = viewOf(state);
  const failure = failureOf(state);
  const resultId = resultIdOf(state);

  /**
   * Rebuild the page requests when a **result** arrives — not on every render,
   * and not on selection.
   *
   * `resultIdOf` is `null` while a run is in flight and the run's token once its
   * result is on screen, so this fires exactly at the transition that produces
   * something to render. The previous key was `runTokenOf`, which returned the
   * same token for `running` and `complete`; it therefore never fired at that
   * transition, left `requests` at the `null` it had set during `running`, and
   * the PDF branch below — which needs a non-null `requests` — rendered no page
   * and no rectangle for any completed run (T105).
   *
   * What the requests should be is `requestsForResult`'s answer, so the decision
   * is a tested function rather than a line in an effect.
   */
  useEffect(() => {
    setRequests(requestsForResult(state));
    // `state` is deliberately not a dependency: it changes identity on every
    // selection and tick, and neither produces a new result to rebuild for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultId]);

  // Pure: it computes the next state and does nothing else. The side effects a
  // selection implies — asking for the page and scrolling to it — belong to the
  // effect below, not in here (T098). They were in here, and `main.tsx` mounts
  // under `StrictMode`, which invokes an updater twice precisely to surface
  // that: the scroll was scheduled twice and the setter called twice per click.
  const onSelect = useCallback((fieldPath: string) => {
    setState((previous) => {
      const current = viewOf(previous);
      if (current === null) return previous;
      const selected = select(current, fieldPath);

      return previous.kind === "complete"
        ? { ...previous, view: selected }
        : previous.kind === "failed"
          ? { ...previous, failure: { ...previous.failure, survivors: selected } }
          : previous;
    });
  }, []);

  /**
   * Bring the selected field's page into view (US1/AC3).
   *
   * Which page is the model's answer — the *first* of however many the field
   * touches, with the rest visible in its row (FR-021, FR-022). Two things have
   * to happen in order, and that ordering is why this is an effect: the page may
   * not be rendered yet, so it is requested, and only once React has committed
   * that request does an element exist to scroll to. Depending on `requests` is
   * what makes the second pass happen after the first has mounted.
   *
   * The previous version queued a microtask and called it "after paint". A
   * microtask flushes before paint, so scrolling to a just-requested page
   * targeted a ref React had not registered — the one case `requestPage` was put
   * here to handle.
   *
   * `requestPage` returns the same object when the page is already asked for, so
   * this settles after one extra pass rather than looping.
   *
   * **Once per selection, and only once.** The effect has to watch `requests` to
   * catch the page it just asked for, which means it also runs when the user
   * asks for some *other* page from the navigation — and scrolling then would
   * drag them back to the selected field the moment they tried to leave it.
   * `scrolledFor` is what makes "the selection changed" the trigger rather than
   * "something changed".
   */
  useEffect(() => {
    if (view === null || view.selection === null) return;

    const [target] = pagesForSelection(view);
    if (target === undefined) return;

    setRequests((existing) => (existing === null ? existing : requestPage(existing, target)));

    if (scrolledFor.current === view.selection) return;
    const element = pageRefs.current.get(target);
    // Not mounted yet: the request above will bring it, and the pass that
    // follows the commit will find it.
    if (element === undefined) return;

    scrolledFor.current = view.selection;
    element.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [view, requests]);

  const holdPage = useCallback((pageIndex: number, element: HTMLDivElement | null) => {
    if (element === null) pageRefs.current.delete(pageIndex);
    else pageRefs.current.set(pageIndex, element);
  }, []);

  const overlayFor = (pageIndex: number) =>
    view === null ? null : (
      <Overlay
        boxes={view.boxesByPage.get(pageIndex) ?? []}
        selection={view.selection}
        onSelect={onSelect}
      />
    );

  return (
    <Stack direction="vertical" gap={4} padding={4}>
      <Heading level={1}>docdoc — grounding viewer</Heading>
      <Text size="sm">
        Read-only. Nothing here edits a value, and no request it makes writes anything the
        deployment keeps.
      </Text>

      <Picker
        schemas={schemas ?? []}
        emptyNotice={emptyRegistryNotice(schemas)}
        selectedSchema={schema}
        canRun={canStartRun(state) && schema !== null && isReadyToRun(doc, pdf !== null)}
        onDocument={(file) => void onDocument(file)}
        onSchema={setSchema}
        onRun={onRun}
      />

      {doc?.notice == null ? null : (
        <Banner status="info" title="No page image for this document" description={doc.notice} />
      )}

      {openingNotice(doc, pdf !== null) === null ? null : (
        <Text size="sm">{openingNotice(doc, pdf !== null)}</Text>
      )}

      <Running label={waitingLabel(state)} />
      {failure === null ? null : (
        <Banner
          status="error"
          title={failureTitle(failure)}
          description={failureNotice(failure)}
        />
      )}

      {view === null ? null : (
        <Stack direction="horizontal" gap={6} align="start">
          <Stack direction="vertical" gap={2}>
            {doc?.kind === "pdf" && pdf !== null && requests !== null ? (
              <>
                <PageNav
                  notice={selectivityNotice(view)}
                  pages={reachablePages(view)}
                  shown={rendered(requests)}
                  onRequest={(pageIndex) =>
                    setRequests((previous) =>
                      previous === null ? previous : requestPage(previous, pageIndex),
                    )
                  }
                />
                {rendered(requests).map((pageIndex) => (
                  <div key={pageIndex} ref={(element) => holdPage(pageIndex, element)}>
                    <Page pdf={pdf} pageIndex={pageIndex} width={PAGE_WIDTH}>
                      {overlayFor(pageIndex)}
                    </Page>
                  </div>
                ))}
              </>
            ) : null}

            {doc?.kind === "image" && bytes !== null && doc.mediaType !== null ? (
              <div ref={(element) => holdPage(0, element)}>
                <ImagePage bytes={bytes} mediaType={doc.mediaType} width={PAGE_WIDTH}>
                  {overlayFor(0)}
                </ImagePage>
              </div>
            ) : null}
          </Stack>

          <Stack direction="vertical" width="fill">
            <ValueList values={view.values} selection={view.selection} onSelect={onSelect} />
          </Stack>
        </Stack>
      )}
    </Stack>
  );
}
