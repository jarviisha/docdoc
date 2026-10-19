# OCR gateway: docdoc as the OCR provider for other systems

Status: design, awaiting approval. Date: 2026-10-19.

## Intent

Other systems (the first is **mmerp**, called "system A" below) need OCR and document
processing, and they want to call **one** service instead of writing an integration for each
OCR engine or cloud provider. docdoc becomes that service: A sends a document, picks a provider
by name from the ones docdoc has configured, optionally passes options, and gets back raw OCR
(text, positions, tables where the provider supplies them) in one provider-neutral shape.

docdoc runs **no model itself**. Every OCR engine is a separate HTTP service, which docdoc reaches
through configuration. To make the open-source engines easy to stand up, docdoc ships
**deployment kits**: compose files that run a model behind an HTTP interface docdoc can call.

## Current state (verified 2026-10-19)

- `ingest` already has the abstraction: the `Parser` protocol (`src/docdoc/ingest/parser.py`),
  capability-based selection (`src/docdoc/ingest/registry.py`), and a provider-neutral
  `Document` output (text, pages, tokens with normalized bbox and `source_confidence`, blocks,
  tables, provenance). Parsers today: `pdf-text`, `azure-di`, `gcv`.
- `default_registry()` hard-codes those three parsers. Adding one means editing docdoc.
- No HTTP route returns raw OCR. Every extraction route requires a `schema`. Only the CLI has
  `docdoc parse`.
- Callers cannot name a parser. Principle IV says that parsers are selected by declared
  capability and never by a hard-coded provider name.
- On the OCR host `192.168.100.27`, two services run, both on `POST /ocr` with the
  **PaddleX serving protocol**:
  - `paddlex-ocr`: official `paddlex --serve`, pipeline `OCR` (PP-OCRv5 server det/rec, CPU),
    config `~/paddlex-serving/OCR.yaml`, `max_num_input_imgs: 3`.
  - `tesseract-ocr`: a hand-written FastAPI shim (`~/tesseract-ocr/app.py`) that imitates the
    PaddleX `/ocr` request and response so that "mmerp calls it without changes". It renders PDFs
    with `pypdfium2` at 300 dpi and handles `vie` only. It **silently truncates PDFs to 3 pages**,
    and its `numPages` reports pages *read*, not pages in the file. It returns no
    `rec_scores`. It reports failures as HTTP 200 with `errorCode: 500` and the exception class
    name.
  - Both are exposed through nginx at `https://paddleocr-dev.foxai.com.vn` (PaddleX at `/`,
    Tesseract at `/tesseract/`) behind an auth include. The auth type is not yet known.
- The observed response, for one page: `result.ocrResults[i].prunedResult` carries `rec_texts`,
  `rec_boxes` (`[x1,y1,x2,y2]`, pixels of the rendered page), plus `rec_scores` and `rec_polys`
  (PaddleX only). `result.dataInfo` carries `type` and per-page `width`/`height`. Results are at
  **line** granularity. Render sizes differ by service (1190×1684 against 2474×3500 for the same
  page).

## Goals

1. **A raw OCR API.** A storeless HTTP route that takes a document and returns OCR output, with
   no schema, no extraction, no grounding, and no validation.
2. **The caller picks the provider.** A request may name one of the providers docdoc has
   configured, and may pass per-request options (language and similar). With no provider named,
   capability-based selection works exactly as it does today.
3. **Discovery.** A route that lists the configured providers, what each can do (capabilities,
   media types), and whether each is usable right now, with the reason when it is not. The
   registry already holds this (`available`, `unavailable_reason`).
4. **One adapter for the PaddleX `/ocr` protocol**, configured with any number of named endpoints
   (`paddle` → URL1, `tesseract` → URL2, …). Any model wrapped in that protocol plugs in without
   code. This adapter is how `default_registry()` stops being a fixed list for OCR.
5. **Deployment kits**, separate from the main `packaging/docker/compose.yml`:
   - **PaddleX kit**: compose and a pipeline config that run the **official** PaddleX serving
     interface unchanged. docdoc writes no wrapper around it.
   - **Tesseract kit**: compose, and an HTTP server written and shipped by docdoc that speaks the
     **PaddleX `/ocr` protocol**, because Tesseract has no official HTTP interface. Compared with
     the current shim it must: never truncate silently; return per-line confidence (Tesseract's
     TSV already carries `conf`); fail with real HTTP status codes; and take its languages from
     configuration.
6. **Provenance keeps saying who read it.** Every result names the provider id, its version (what
   the endpoint reports, plus the adapter version), and the options used, as provenance does
   today.

## Non-goals

- **No model in docdoc's process or in docdoc's main compose.** The main composition stays api,
  worker, postgres, and object storage. Kits are separate compose files, typically on another
  host.
- **No caller-supplied credentials.** Provider credentials (URLs, keys, headers) live only in
  docdoc's configuration. A can choose among configured providers and cannot add, reconfigure, or
  see the credentials of any.
- **No async OCR in this project.** The raw OCR route is synchronous, within the existing size and
  page limits. Async OCR-only runs come later, if the sync route proves too slow.
- **No third-party plugin mechanism** (entry points and similar). The PaddleX-protocol adapter
  with configured endpoints covers the stated need. Add plugins when someone needs a provider
  that cannot speak that protocol.
- No changes to the extraction, grounding, or validation pipeline beyond letting it benefit from
  the new providers.

## Anti-goals (would make this a failure even if it works)

- **Silent fallback.** If A names a provider that cannot serve the request (wrong media type,
  missing capability, unavailable), docdoc refuses with a typed error naming the provider and the
  reason. It never quietly switches provider.
- **Silent partial results.** A document that comes back with fewer pages than it has is an
  error, never a quietly shorter result. This holds for both kits and for any configured endpoint.
- **Credential leakage.** No provider URL secret, key, or header appears in a response, a log, a
  discovery listing, or an error message.
- **Becoming a vendor wrapper.** A's response shape must not change with the provider. Any field
  only one provider supplies (polygons, confidence) is optional in the neutral shape, and its
  absence is explicit.

## Constraints

- **Constitution.** Goal 2 conflicts with Principle IV ("never by hard-coded provider name"), and
  forcing OCR on a text PDF because A picked an OCR provider bends Principle V. This project
  includes a constitution amendment and an ADR (next number: 0021) recording that *a request may
  name a configured provider. Code never does, and capability selection remains the default.*
  ADR-0001's "`OCRProvider` is deferred" stands: the PaddleX-protocol adapter satisfies `Parser`.
- `Parser` contract ING-7: one complete `Document` or an exception. This is what the
  "silent partial" anti-goal enforces.
- Provider SDK and HTTP-client types stay inside `ingest/parsers/`. Errors map to docdoc's error
  model (`ProviderError` and similar) and never leak.
- Base install unchanged. The PaddleX-protocol adapter should use the standard library HTTP
  client, as `examples/submit_async_run.py` does, unless an existing dependency is clearly better.
  PDF splitting needs `pypdfium2` (Apache-2.0/BSD-3, not PyMuPDF). It goes in a new opt-in extra
  (Claude's call: `docdoc[ocr]`), which is added to the Docker image's default `DOCDOC_EXTRAS`.
  Without the extra, the adapter still serves images; a PDF of any length is refused with a typed
  error naming the missing extra, even one within the endpoint's page limit, because without a
  PDF reader the adapter cannot learn the page count and so cannot prove the result it gets back
  is complete (Task 3, deviation from the original plan recorded there).
- Licensing stays clean. The Tesseract server uses `pypdfium2` (Apache/BSD) to render PDFs, not
  PyMuPDF (AGPL). Tesseract and PaddleOCR are Apache-2.0.
- The Tesseract server is its own deployable. It must not import `docdoc`.

## Decided by the user

- A decides the provider per request and may pass options.
- Credentials are docdoc's configuration alone.
- No model runs with docdoc's compose. docdoc reaches models through configured APIs.
- docdoc ships deployment kits: PaddleX uses its official HTTP interface, and Tesseract gets a
  docdoc-provided HTTP server.
- The docdoc Tesseract server speaks the PaddleX `/ocr` protocol.
- A sends both multi-page scanned PDFs and single images (JPG/PNG).
- **The response is a docdoc-neutral shape**: the serialized `Document` (text, pages, normalized
  bbox, confidence, tables, provenance). It does not imitate PaddleX. mmerp changes its result
  reader once.
- **docdoc splits long PDFs itself.** An endpoint declares a page limit in its configuration.
  The adapter splits a longer PDF into sub-PDFs within that limit, sends them in order, and
  stitches the results back into one `Document` with correct page indices. Any chunk failing
  fails the whole parse (ING-7).
- **The docdoc Tesseract server replaces the hand-written shim on .27** once the kit is done.
  Direct callers of the shim will see real HTTP status codes, no truncation, and added scores.
  The actual deployment on .27 is confirmed with the user at the time it happens.

## Calls made by Claude (open to correction)

- **Order of work:** (1) the PaddleX-protocol adapter and endpoint configuration, tested against
  the live services on .27; (2) the raw OCR route with provider choice and the discovery route;
  (3) the Tesseract server and kit; (4) the PaddleX kit.
- **Kit location:** `packaging/ocr/paddlex/` and `packaging/ocr/tesseract/` (the server source
  lives inside the latter).
- **Endpoint configuration:** a JSON file named by an environment variable
  (`DOCDOC_OCR_ENDPOINTS_FILE`), following the `DOCDOC_*_FILE` convention and mounted through
  `packaging/docker/secrets/`. Each entry gives an id, URL, optional auth headers, declared
  capabilities, and media types.
- **Without a provider named**, configured endpoints join capability selection with the
  existing priority rules (`DEFAULT_PRIORITY` extended by configuration).
- **Line granularity.** One PaddleX text line maps to one token-bearing line in the `Document`.
  Splitting into words is not attempted.
- **Bbox only, no polygons.** `rec_polys` is dropped and `Geometry` stays a normalized bbox. Add
  polygons to the kernel when a consumer needs them.
- **Split by sub-PDF, not by image.** Chunks are sent as PDFs so each service keeps its own
  rendering. Chunks run sequentially in v1; parallel chunks are a later optimisation.
- **Nginx auth on .27 is still unknown** (the include file is not readable to the `ubuntu`
  user). The endpoint configuration takes arbitrary request headers, which covers both basic and
  bearer auth.
- **A short result is an error.** If an endpoint (or a chunk) returns fewer pages than it was
  sent, the adapter raises a typed `ProviderError` naming the endpoint and the counts. There is no partial
  flag in `IngestProvenance`, and ING-7 stands.
- **Options are a docdoc vocabulary, not vendor passthrough.** v1 has one option, `language`.
  Each endpoint declares its languages in the endpoint configuration, and a request for an
  undeclared language is refused with a typed error before any call. Language never selects the
  endpoint: the caller names the endpoint, or capability selection picks one.
- **Discovery is static plus a probe.** Availability from configuration, as the registry does
  today, plus a live health probe that only the discovery route runs. `plan_parse` stays
  network-free. An endpoint that is down at parse time fails as `ProviderError`.

## Left to the builder

- The exact route paths and the request encoding (multipart against raw body against JSON base64).
- How `version` is obtained for a configured endpoint. If the service reports none, the
  configuration supplies one, and it is part of document identity.
- Retry, timeout, and transport settings, reusing `ingest/retry.py` and `TransportSettings`.
- Whether PaddleX truncates or refuses over `max_num_input_imgs`. Verify against the live
  service, then enforce the "short result is an error" call accordingly.
- The test strategy, which at minimum needs: adapter mapping from recorded responses
  (`recording/` exists for this); a contract test that the Tesseract server's output parses
  through the adapter; and secrets absent from discovery and error output.
