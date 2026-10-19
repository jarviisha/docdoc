# OCR Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let other systems call docdoc for raw OCR: name a configured provider, get back a
provider-neutral `Document`. Ship deployment kits for PaddleX (official serving) and Tesseract
(a docdoc-written server speaking the PaddleX `/ocr` protocol).

**Architecture:** One new `Parser` adapter (`ingest/parsers/paddlex.py`) speaks the PaddleX
`/ocr` protocol and is instantiated once per endpoint listed in a JSON configuration file.
`default_registry()` registers those endpoints. `parse()` gains a `parser=` argument, so a
caller can name one, and refuses rather than falls back. The API adds a storeless `POST /v1/ocr`
and `GET /v1/parsers`. Kits live under `packaging/ocr/`. The Tesseract server there never
imports `docdoc`.

**Tech Stack:** Python 3.11, pydantic, stdlib `urllib` for the adapter's HTTP, `pypdfium2`
(new `docdoc[ocr]` extra) to count and split PDFs, FastAPI for the routes and the Tesseract
server, Docker Compose for the kits.

**Spec:** `docs/superpowers/specs/2026-10-19-ocr-gateway-design.md`

## Global Constraints

- The vocabulary is **`parser`** everywhere (library argument, query parameter, route name),
  matching `parser_id` in provenance and errors. "Provider" appears only in prose.
- No silent fallback: a named parser that is unknown, unavailable, or cannot serve the media type
  or capabilities raises `ParserCapabilityError` (HTTP 422). It never switches parser.
- No silent partial result: an endpoint or chunk returning a different page count from what was
  sent raises `ProviderError(reason="service")`. ING-7 stands.
- No endpoint URL or header value appears in any error message, log event, `repr`, or HTTP body.
  Errors name the endpoint by `id` only.
- The base install is unchanged. `pypdfium2` only in `docdoc[ocr]`, which is added to the Docker
  image's default `DOCDOC_EXTRAS`. Not PyMuPDF.
- `pypdfium2` and `urllib` usage confined to `docdoc.ingest.parsers.paddlex`, declared in the
  import-linter contract "provider SDKs stay inside parser adapters".
- No model and no fifth service in `packaging/docker/compose.yml`.
- `packaging/ocr/tesseract/server/` imports no `docdoc` module.
- PR gates (CONTRIBUTING.md): `HYPOTHESIS_PROFILE=thorough uv run pytest`,
  `uv run mypy src/docdoc/kernel`, `uv run ruff check . && uv run ruff format --check .`,
  `uv run lint-imports`.
- Commits follow the repo's style (`feat(012): …`), explain *why*, and end with the
  `Co-Authored-By` trailer.

## Review Focus

1. **A page rotated by the scanner** (PaddleX `doc_preprocessor_res.angle` ≠ 0): boxes must
   still land inside the page, or the parse fails with a typed `GeometryError`. Never a crash, and
   never geometry quietly off the page. Pinned in Task 2 with a recorded `rotated_90.pdf` response.
2. **A blank page in the middle of a scan**: the page is kept, with an empty span, so page
   indices stay true. Only a document with no text on any page raises
   `ParserError(reason="empty_result")`. Pinned in Task 2.
3. **Secrets in a failure**: an endpoint with `Authorization` header `Basic SEKRIT` and URL
   `https://user:pw@ocr.internal` fails in every way (401, 500, timeout, refused connection,
   garbage body). Neither `SEKRIT`, `pw`, nor the host appears in `str(error)`, in the HTTP body,
   or in `GET /v1/parsers`. Pinned in Tasks 3 and 5.
4. **HTTP 200 carrying `errorCode` ≠ 0** (the old shim's failure style) raises
   `ProviderError(reason="service")`, not a mapping crash. Pinned in Task 2.
5. **Encrypted or corrupt PDF sent to a PaddleX endpoint** raises `UnsupportedDocumentError`
   (`encrypted` / `corrupt`) **before** any network call. Pinned in Task 3.

---

### Task 1: Permit naming a parser — constitution amendment and ADR-0021

**Files:**
- Modify: `.specify/memory/constitution.md` (Principle IV rules; Principle V; the Sync Impact
  Report block at the top, following the format of the existing entries; bump the version as the
  file's own versioning rules say)
- Create: `docs/adr/0021-caller-named-parsers-and-ocr-endpoints.md`
- Modify: `docs/adr/README.md` (index line)

- [ ] **Step 1: Write ADR-0021** in the format of `docs/adr/0020-retire-the-grounding-viewer.md`.
  It records:
  - A request may name a configured parser. Code never does, and capability selection remains
    the default.
  - A named recognition parser on a text PDF is honoured, and the text-layer verdict is still
    computed and recorded as overridden (`rule.as_override`).
  - Endpoints speaking the PaddleX `/ocr` protocol are configured, not coded.
  - `OCRProvider` stays deferred (ADR-0001): the adapter satisfies `Parser`.
  - docdoc runs no model. Kits are separate compositions.
- [ ] **Step 2: Amend Principle IV.** Replace "Parsers are selected by declared capabilities,
  never by hard-coded provider name." with: "Parsers are selected by declared capabilities, never
  by a provider name hard-coded in docdoc. A request may name a configured parser (ADR-0021).
  The request then fails rather than falling back." Amend Principle V to say an explicitly named
  recognition parser overrides routing, with the verdict kept.
- [ ] **Step 3: Verify** `uv run pytest tests/unit -q -k "constitution or adr or documented"`.
  Expected: PASS. Several tests check that documents reference real things.
- [ ] **Step 4: Commit** `docs(012): let a request name a configured parser (ADR-0021)`

---

### Task 2: PaddleX response mapping and endpoint configuration (offline)

**Files:**
- Create: `src/docdoc/ingest/parsers/paddlex.py` (configuration plus mapping half)
- Create: `tests/fixtures/paddlex/record.py`, plus recorded JSON files (below)
- Create: `tests/unit/test_paddlex_mapping.py`, `tests/unit/test_ocr_endpoints.py`

**Interfaces:**
- Produces, in `docdoc.ingest.parsers.paddlex`:
  - `ENDPOINTS_FILE_ENV: Final = "DOCDOC_OCR_ENDPOINTS_FILE"`
  - `ADAPTER_VERSION: Final = "1.0.0"`
  - `READING_ORDER: Final = "paddlex-line-order@1"`
  - `class OcrEndpoint(BaseModel)`, frozen, `extra="forbid"`:
    - `id: str` (pattern `^[a-z][a-z0-9-]*$`)
    - `url: str = Field(repr=False)` (must start `http://` or `https://`)
    - `version: str` (min length 1)
    - `headers: dict[str, str] = Field(default_factory=dict, repr=False)`
    - `media_types: frozenset[str] = frozenset({PDF, JPEG, PNG})`
    - `languages: tuple[str, ...] = ()`
    - `max_pages: int | None = Field(default=None, ge=1)`
  - `load_endpoints(path: str | Path) -> tuple[OcrEndpoint, ...]` reads `{"endpoints": [...]}`.
    It raises `ValueError` naming the file and the entry's **index**, never its values, on a bad
    entry, a duplicate id, or an id equal to a built-in (`pdf-text`, `azure-di`, `gcv`).
  - `class OcrLine(NamedTuple)`: `text: str`, `box: tuple[float, float, float, float]`
    (pixels, x1 y1 x2 y2), `score: float | None`
  - `class OcrPage(NamedTuple)`: `width: float`, `height: float`, `lines: tuple[OcrLine, ...]`
  - `pages_of(response: Mapping[str, Any], *, parser_id: str, blob_id: str) -> list[OcrPage]`
  - `build_document(pages: Sequence[OcrPage], *, source: SourceFile, options: Mapping[str, Any], text_layer: TextLayerRecord | None, parser_id: str, parser_version: str, capabilities: ParserCapabilities) -> Document`

- [ ] **Step 1: Record fixtures from the live services on .27.**
  1. Ask the user for the SSH password.
  2. Open a tunnel: `ssh -N -L 18080:127.0.0.1:8080 -L 18081:127.0.0.1:8081 ubuntu@192.168.100.27`.
  3. Write `tests/fixtures/paddlex/record.py` (stdlib only). It POSTs
     `{"file": <b64>, "fileType": 0|1, "visualize": false}` to `/ocr`. From each page it keeps
     only `prunedResult.{rec_texts,rec_boxes,rec_scores,doc_preprocessor_res}`, plus
     `result.dataInfo`, `errorCode`, and `errorMsg`, so base64 images never enter the repo.
     Then it writes JSON.
  4. Record these files into `tests/fixtures/paddlex/`:
     - `paddle.digital_invoice.json`, from `tests/fixtures/pdf/digital_invoice.pdf`.
     - `paddle.scanned_contract.json` (2 pages).
     - `paddle.rotated_90.json`.
     - `paddle.sample_page.json`, from `tests/fixtures/image/sample_page.png`. Its `dataInfo` has
       type `image`.
     - `paddle.mixed_pages.json` (3 pages).
     - `tesseract-shim.digital_invoice.json`, from port 18081. It has no `rec_scores`.
     - `paddle.blank_page.json`. This one is **hand-made**: copy `paddle.scanned_contract.json`
       and empty page 2's `rec_texts`, `rec_boxes`, and `rec_scores`.
  5. Send `paddle.mixed_pages` with 3 pages and record whether PaddleX truncates or refuses over
     `max_num_input_imgs: 3`. Also send a 4-page PDF built with pypdfium2. Write the observed
     behaviour as a comment at the top of `record.py`.
- [ ] **Step 2: Write failing tests.** In `test_ocr_endpoints.py`:
  - `test_loads_endpoints_with_defaults`
  - `test_rejects_duplicate_ids`
  - `test_rejects_builtin_id`
  - `test_rejects_non_http_url`
  - `test_repr_hides_url_and_headers`: `repr(endpoint)` lacks the URL and the header value.
  - `test_error_names_index_not_values`: a bad entry holding header value `SEKRIT` raises a
    `ValueError` whose message contains `"endpoints[1]"` and not `"SEKRIT"`.

  In `test_paddlex_mapping.py`, using the recordings:
  - `test_invoice_maps_to_valid_document`: `validate_output` passes, `"INV-001"` is in
    `document.text`, and `len(document.pages) == 1`.
  - `test_each_line_is_one_token`: the token count equals `len(rec_texts)`.
  - `test_boxes_are_normalized_against_datainfo_page_size`: the first token's bbox equals
    `rec_boxes[0]` divided by `dataInfo.pages[0]` width and height.
  - `test_scores_become_source_confidence`, and `test_missing_scores_are_none`, which uses the
    shim recording.
  - `test_image_response_uses_top_level_size`, which uses `paddle.sample_page.json`.
  - `test_multi_page_keeps_page_order`, which uses scanned_contract.
  - `test_blank_middle_page_is_kept` (Review Focus 2): with `paddle.blank_page.json`, the
    document has 2 pages and `document.pages[1].span` is empty.
  - `test_all_pages_blank_is_empty_result`: raises `ParserError` with `reason == "empty_result"`.
  - `test_rotated_page_stays_on_page` (Review Focus 1): either every bbox is within `[0, 1]`, or
    the mapping raises `GeometryError`. Assert whichever the recording produces, and comment why.
  - `test_error_code_in_200_body_is_provider_error` (Review Focus 4): `{"errorCode": 500,
    "errorMsg": "ValueError"}` raises `ProviderError` with `reason == "service"`.
  - `test_length_mismatch_is_parser_error`: `rec_texts` and `rec_boxes` of different lengths
    raise `ParserError` with `reason == "internal"`.
  - `test_provenance_names_endpoint`: `parser_id == "paddle"` and
    `reading_order == "paddlex-line-order@1"`.
- [ ] **Step 3: Run** `uv run pytest tests/unit/test_paddlex_mapping.py tests/unit/test_ocr_endpoints.py -q`.
  Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 4: Implement the configuration and mapping half of `paddlex.py`.**
  - Use `DocumentBuilder(geometry=True)`. Each `OcrLine` becomes
    `builder.add_line([(text, normalize_bbox(..., width, height, page_index), score)])`.
  - Call `start_page(width, height, rotation=0)` for every page, including empty ones.
  - Page sizes come from `dataInfo.pages[i]` for `type == "pdf"`, and from
    `dataInfo.width/height` for `type == "image"`.
  - Build `IngestProvenance` as `gcv._map_annotate_result` does, with `text_layer_used=False`.
  - Wrap unexpected exceptions into `ParserError(reason="internal")` as `gcv._translated` does.
  - The module docstring explains the protocol choice and cites ADR-0021.
- [ ] **Step 5: Run the tests again.** Expected: PASS.
- [ ] **Step 6: Commit** `feat(012): map the PaddleX /ocr protocol to a Document`

---

### Task 3: The PaddleX parser over the wire — HTTP, retries, PDF splitting, languages

**Files:**
- Modify: `src/docdoc/ingest/parsers/paddlex.py` (add the parser class)
- Modify: `pyproject.toml`:
  - Add the extra `ocr = ["pypdfium2>=4.30"]`.
  - In the import-linter ingest contract, add `"pypdfium2"` to `forbidden_modules` and
    `"docdoc.ingest.parsers.paddlex -> pypdfium2"` to `ignore_imports`.
- Modify: `packaging/docker/compose.yml` and `packaging/docker/Dockerfile`: add `ocr` to the
  default `DOCDOC_EXTRAS` in both places.
- Create: `tests/unit/test_paddlex_parser.py`, `tests/integration/test_paddlex_live.py`

**Interfaces:**
- Consumes: Task 2's `OcrEndpoint`, `pages_of`, `build_document`.
- Produces: `class PaddleXParser` satisfying `Parser`:
  - `__init__(self, endpoint: OcrEndpoint, post: Callable[[OcrEndpoint, dict[str, Any], float], Mapping[str, Any]] | None = None)`.
    `post` takes the endpoint, the JSON body, and a timeout in seconds, and returns the decoded
    JSON. Defaults to the urllib implementation.
  - `id` = `endpoint.id`
  - `version` = `f"{ADAPTER_VERSION}+{endpoint.version}"`
  - `capabilities` = `ParserCapabilities(text=True, geometry=True, tables=False,
    handwriting=False, media_types=endpoint.media_types, requires_network=True)`
  - `reading_order` = `READING_ORDER`
  - `languages: tuple[str, ...]` = `endpoint.languages`
  - `parse(source, options, transport, text_layer=None) -> Document`
  - `probe(self, timeout_s: float = 2.0) -> bool`: GET `{url}/health`, true when `errorCode`
    is 0. Never raises.

- [ ] **Step 1: Write failing tests** in `test_paddlex_parser.py`. They use a fake `post` that
  records calls and replays Task 2's recordings.
  - `test_image_is_sent_once_as_filetype_1`
  - `test_pdf_within_limit_is_sent_whole_as_filetype_0`
  - `test_pdf_over_limit_is_split_and_stitched`: a 5-page PDF built in the test with pypdfium2
    and an endpoint with `max_pages=2` give 3 calls carrying 2, 2, and 1 pages. The result has 5
    pages in order, and every token's `page_index` matches its chunk offset.
  - `test_short_chunk_is_provider_error`: the fake returns 1 page for a 2-page chunk, which
    raises `ProviderError` with `reason == "service"` and a message containing `"paddle"`, `"1"`,
    and `"2"`.
  - `test_one_failing_chunk_fails_whole_parse`
  - `test_language_not_declared_is_refused_before_any_call`: `options={"language": "fra"}` on an
    endpoint declaring `("vie",)` raises `ParserCapabilityError`, and `post` is never called.
  - `test_unknown_option_is_refused`
  - `test_encrypted_pdf_refused_before_any_call` (Review Focus 5): with
    `tests/fixtures/pdf/encrypted.pdf`, raises `UnsupportedDocumentError` with
    `reason == "encrypted"`, and `post` is never called. Likewise corrupt bytes beginning
    `%PDF-` give `reason == "corrupt"`.
  - `test_http_status_mapping`: run the urllib `post` against a local
    `http.server.ThreadingHTTPServer` started in the test.
    | Status | Result |
    |---|---|
    | 401, 403 | `ProviderError` reason `auth` |
    | 429 with `Retry-After: 3` | reason `rate_limit`, `retry_after_s == 3.0` |
    | 400, 415, 422 | `UnsupportedDocumentError` reason `corrupt` |
    | 500, 502, 503 | reason `service` |
    | 504 | reason `timeout` |
    | non-JSON body | `ProviderError` reason `service` |
    | refused connection (closed port) | reason `transport` |
  - `test_no_secret_in_any_failure` (Review Focus 3): repeat every case above with an endpoint
    whose URL contains `user:pw@` and whose header is `Authorization: Basic SEKRIT`. For each,
    assert that `"SEKRIT"`, `"pw@"`, and `"127.0.0.1"` are absent from `str(error)` and from
    `str(error.__cause__)`.
  - `test_headers_are_sent`: the local server receives the configured header.
  - `test_probe_false_when_unreachable`
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_paddlex_parser.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement `PaddleXParser`.**
  - **Option checks first.** Only the key `language` is accepted, and it must be in
    `endpoint.languages`; otherwise raise `ParserCapabilityError` with
    `required=(f"language={value}",)` and `candidates=((endpoint.id, True, None),)`.
  - **PDF page count via pypdfium2.** pypdfium2's `PdfiumError` on open maps to
    `UnsupportedDocumentError` with reason `encrypted` when a password is required, and
    `corrupt` otherwise.
  - **pypdfium2 missing:** a PDF raises `ParserCapabilityError` with candidates
    `((endpoint.id, False, "extra_not_installed"),)`. Images still work.
    **Deviation from the spec, recorded:** the spec said PDFs within the limit would still work
    without the extra. They do not, because without a PDF reader docdoc cannot know the page
    count, and so cannot prove the result is complete. Update that line of the spec in this
    commit.
  - **Splitting:** chunks of `endpoint.max_pages` pages, made with
    `PdfDocument.new()` + `import_pages`, sent sequentially. Each chunk goes through
    `analyze_with_retries`.
  - **Per-call timeout:** `min(transport.attempt_timeout_s, deadline.remaining_s)`.
  - **Request body:** `{"file": b64, "fileType": 0|1}`.
  - **Error messages** say `f"endpoint {endpoint.id!r} ..."`. Raise translated errors with
    `from None`, so the urllib exception, which carries the URL, is not chained.
- [ ] **Step 4: Write `tests/integration/test_paddlex_live.py`**, marked `provider`. It is
  skipped unless `DOCDOC_TEST_PADDLEX_URL` is set; it also reads `DOCDOC_TEST_TESSERACT_URL`.
  It parses `digital_invoice.pdf` through each configured endpoint and asserts `"INV-001"` is in
  the text.
- [ ] **Step 5: Run the checks.**
  - `uv sync --all-extras && uv run pytest tests/unit/test_paddlex_parser.py -q && uv run lint-imports`.
    Expected: PASS.
  - With the tunnel from Task 2:
    `DOCDOC_TEST_PADDLEX_URL=http://127.0.0.1:18080 DOCDOC_TEST_TESSERACT_URL=http://127.0.0.1:18081 uv run pytest -m provider tests/integration/test_paddlex_live.py`.
    Expected: PASS.
- [ ] **Step 6: Commit** `feat(012): call PaddleX-protocol endpoints, splitting long PDFs`

---

### Task 4: Configured endpoints in the registry, and naming a parser in `parse()`

**Files:**
- Modify: `src/docdoc/ingest/registry.py`: `default_registry`, plus a new `select_named`
- Modify: `src/docdoc/ingest/parse.py`: `parse`, `plan_parse`, `_select`
- Modify: `src/docdoc/pipeline/runner.py:752-760`, `_plan_parse`, to pass `parser` through
  (default `None`)
- Create: `tests/unit/test_named_parser.py`

**Interfaces:**
- Consumes: Task 3's `PaddleXParser`, and Task 2's `load_endpoints` and `ENDPOINTS_FILE_ENV`.
- Produces:
  - `ParserRegistry.select_named(self, parser_id: str, require: CapabilityRequest) -> Parser`
    raises `ParserCapabilityError`:
    - **Unknown id:** the message lists the known ids.
    - **Unavailable:** the message includes `unavailable_reason`.
    - **Does not satisfy `require`:** the message gives `missing_for` or the media type.
  - `parse(..., parser: str | None = None)` and `plan_parse(..., parser: str | None = None)`.
  - `default_registry()` registers one `PaddleXParser` per endpoint in `$DOCDOC_OCR_ENDPOINTS_FILE`
    when that is set, available. Ranking is unchanged: unlisted ids rank after
    `DEFAULT_PRIORITY`, by id.

- [ ] **Step 1: Write failing tests** in `test_named_parser.py`, using a registry holding a
  `PaddleXParser` with a fake `post`:
  - `test_named_parser_is_used`
  - `test_unknown_name_refused_listing_known`
  - `test_unavailable_name_refused_with_reason`
  - `test_named_parser_wrong_media_type_refused_not_fallen_back`: a PNG-only endpoint given a PDF
    raises `ParserCapabilityError`, even though `pdf-text` could serve it.
  - `test_named_recognition_parser_on_text_pdf_is_honoured`: `digital_invoice.pdf` with
    `parser="paddle"` uses paddle. `document.provenance.text_layer.overridden` is true, and the
    verdict's pages are still present.
  - `test_unnamed_selection_unchanged`: with no `parser`, the same registry still picks
    `pdf-text` for the text PDF.
  - `test_default_registry_reads_endpoints_file`, which uses `monkeypatch.setenv` and `tmp_path`.
  - `test_default_registry_without_env_is_unchanged`: the ids are exactly
    `{"pdf-text", "azure-di", "gcv"}`.
  - `test_named_parser_changes_document_id`: same bytes, `parser="paddle"` against
    `parser="tesseract"`, give different `plan.document_id`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_named_parser.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - In `plan_parse`, when `parser` is given, route with `force = "recognition" if
    named.capabilities.requires_network else "native"`. That reuses `_route`'s override
    recording, and no parser name is hard-coded.
  - Then call `registry.select_named` instead of `_select`, after the same
    media-type-mismatch check `_select` does.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q`. Expected: PASS, with existing selection
  tests untouched.
- [ ] **Step 5: Commit** `feat(012): let a caller name a configured parser, and refuse rather than fall back`

---

### Task 5: HTTP — `POST /v1/ocr` and `GET /v1/parsers`

**Files:**
- Modify: `src/docdoc/api/app.py`: two routes in `_router()`. `_Deployment` gains a
  `parsers: ParserRegistry | None = None` argument and a `parser_registry()` method that returns
  it or `default_registry()`.
- Modify: `src/docdoc/api/models.py`: `ParserListing` and `ParserChoice`
- Modify: `packaging/docker/compose.yml`: forward
  `DOCDOC_OCR_ENDPOINTS_FILE: ${DOCDOC_OCR_ENDPOINTS_FILE:-}`, with a comment that it points
  under `/secrets`
- Modify: `README.md` ("Over HTTP" route list), `docs/concepts/ingest.md` (named parsers and
  endpoints)
- Create: `tests/contract/test_ocr_routes.py`

**Interfaces:**
- Consumes: Task 4's `parse(..., parser=)` and `select_named`; Task 3's `PaddleXParser.probe`.
- Produces:
  - `POST /v1/ocr?parser=<id>&language=<lang>`, with the raw document bytes as the body. It
    returns `200` and `document.model_dump(mode="json")`. It is storeless: it uses
    `_read_capped`, `SourceFile.from_bytes`, and `check_limits` exactly as `extract_storeless`
    does. Both query parameters are optional. `language` becomes `options={"language": ...}`.
  - `GET /v1/parsers` returns
    `{"parsers": [{"id", "available", "unavailable_reason"?, "capabilities": {"text", "geometry", "tables", "handwriting"}, "media_types": [...], "languages"?: [...], "healthy"?: bool}]}`,
    in priority order. Fields that do not apply are **absent**, not null.
    - `healthy` is present only for parsers with a `probe` method.
    - `languages` is present only when non-empty.
  - Both routes sit under the existing authentication, like every `/v1` route.

- [ ] **Step 1: Write failing tests** in `test_ocr_routes.py`, using
  `_Deployment(parsers=<registry with fake-post PaddleXParser ids "paddle" and "tesseract">)`:
  - `test_ocr_returns_document_json`: status 200, the body has `text` containing `"INV-001"`,
    and `provenance.parser_id == "paddle"`.
  - `test_ocr_without_parser_uses_capability_selection`
  - `test_ocr_unknown_parser_is_422`: the body names the requested id and lists the configured
    ones.
  - `test_ocr_language_refused_is_422`
  - `test_ocr_provider_failure_is_502`
  - `test_ocr_stores_nothing`: with a `FileArtifactStore` deployment, the store directory is
    unchanged after the call.
  - `test_parsers_lists_configured_with_absent_not_null`
  - `test_parsers_reports_probe_result`: one fake probe returns True, the other False.
  - `test_no_secret_in_parsers_or_errors` (Review Focus 3, the HTTP half): the endpoint URL and
    header value appear in neither the `/v1/parsers` body nor any `/v1/ocr` error body.
  - `test_routes_require_auth_when_enabled`: follow the existing pattern in
    `tests/contract/test_tenant_isolation.py`.
- [ ] **Step 2: Run** `uv run pytest tests/contract/test_ocr_routes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement the routes and models, the compose line, and the docs.**
- [ ] **Step 4: Run** `uv run pytest tests/contract tests/unit -q`. Expected: PASS. This
  includes `test_compose_forwards_every_credential.py` and `test_deployment_reads_its_environment.py`.
  If either needs the new variable listed, add it as those tests instruct.
- [ ] **Step 5: Commit** `feat(012): serve raw OCR and list the configured parsers over HTTP`

---

### Task 6: The Tesseract server and its kit

**Files:**
- Create: `packaging/ocr/tesseract/server/app.py`, `packaging/ocr/tesseract/server/ocr.py`
- Create: `packaging/ocr/tesseract/requirements.txt`, pinned: `fastapi`, `uvicorn`, `pypdfium2`,
  `pillow`
- Create: `packaging/ocr/tesseract/Dockerfile`: `python:3.11-slim-bookworm` with `tesseract-ocr`,
  `tesseract-ocr-vie`, and `tesseract-ocr-eng`; non-root user; healthcheck on `/health`
- Create: `packaging/ocr/tesseract/compose.yml`: publishes `127.0.0.1:8081:8080`, and its
  comments say to front it with a TLS and auth proxy, as .27 does
- Create: `packaging/ocr/tesseract/README.md`: run it, configure it, and register it in docdoc's
  endpoints file
- Create: `tests/unit/test_tesseract_server.py`, `tests/contract/test_tesseract_speaks_paddlex.py`
- Modify: `pyproject.toml` `dev` extra: add `pillow` (the server tests import it)

**Interfaces:**
- In `ocr.py`, with no docdoc import:
  - `lines_from_tsv(tsv: str) -> list[tuple[str, list[int], float | None]]` returns
    `(text, [x1, y1, x2, y2], score)` per line. It keeps the shim's column split: a gap greater
    than 3× the tallest word height starts a new line. `score` is the mean of the words' `conf`
    divided by 100, ignoring `conf == -1`, or `None` when no word has a confidence.
  - `run_tesseract(png: bytes, langs: str) -> str` returns the TSV text (subprocess, 120 s
    timeout).
  - `pages(data: bytes, file_type: int | None) -> tuple[list[PIL.Image.Image], bool]` renders at
    300 dpi, capped at a long side of 3500 px, as the shim does. It renders **every** page.
- In `app.py`:
  - `POST /ocr` with body `{file: str, fileType?: int, visualize?: bool}` returns
    `{"logId": uuid4, "errorCode": 0, "errorMsg": "Success", "result": {"ocrResults":
    [{"prunedResult": {"rec_texts", "rec_boxes", "rec_scores"}}], "dataInfo": {...}}}`, where
    `numPages` is the **document's** page count.
  - `GET /health` returns `{"errorCode": 0, "errorMsg": "Healthy"}`.
  - Environment: `TESSERACT_LANGS` (default `vie+eng`); `MAX_PAGES` (default unset, meaning no
    cap). Over the cap the server answers 413 and does not truncate.
  - Errors use the PaddleX body shape `{"logId", "errorCode": <status>, "errorMsg"}` **with the
    matching HTTP status**:
    | Condition | Status |
    |---|---|
    | bad base64, or an unreadable file | 400 |
    | over `MAX_PAGES` | 413 |
    | Tesseract failure or timeout | 500 |

- [ ] **Step 1: Write failing tests.** The tests load the server by path with `importlib`, and
  `pytest.importorskip("fastapi")`. In `test_tesseract_server.py`:
  - `test_tsv_lines_and_boxes`: a hand-written TSV fixture string covering two lines, one of
    which has two columns, gives three entries with exact boxes.
  - `test_score_is_mean_conf_over_100_ignoring_minus_one`
  - `test_all_pages_rendered`: a 5-page PDF built with pypdfium2 gives `numPages == 5` and five
    `ocrResults`.
  - `test_max_pages_exceeded_is_413_not_truncated`
  - `test_bad_base64_is_400`
  - `test_tesseract_failure_is_500_with_paddlex_body`

  `run_tesseract` is monkeypatched to return fixture TSV, so no binary is needed.
- [ ] **Step 2: Write the failing contract test** `test_tesseract_speaks_paddlex.py`. The server's
  `/ocr` response for a 2-page PDF, with `run_tesseract` faked, goes through
  `docdoc.ingest.parsers.paddlex.pages_of` and `build_document` and gives a valid 2-page
  `Document` with confidences set.
- [ ] **Step 3: Run** `uv run pytest tests/unit/test_tesseract_server.py tests/contract/test_tesseract_speaks_paddlex.py -q`.
  Expected: FAIL.
- [ ] **Step 4: Implement** the server, Dockerfile, compose file, and README. Port the column
  logic from `~/tesseract-ocr/app.py` on .27; read it over SSH if needed.
- [ ] **Step 5: Run the checks.**
  - The tests from Step 3. Expected: PASS.
  - `docker compose -f packaging/ocr/tesseract/compose.yml up -d --build`, then
    `curl -s localhost:8081/health`, then POST `digital_invoice.pdf`. Expected:
    `INV-001` among `rec_texts`, and `rec_scores` present.
  - `uv run lint-imports`, which must report nothing under `packaging/` importing docdoc. Also
    `grep -rn "import docdoc\|from docdoc" packaging/ocr`, which must print nothing.
- [ ] **Step 6: Commit** `feat(012): a Tesseract server that speaks the PaddleX /ocr protocol`

---

### Task 7: The PaddleX kit, and the example endpoints file

**Files:**
- Create: `packaging/ocr/paddlex/compose.yml`, based on .27's `~/paddlex-serving/compose.yaml`
  (image pinned to `paddlex3.3.11-paddlepaddle3.2.0-cpu`, `mem_limit`, `shm_size`, models
  volume, localhost-only port 8080)
- Create: `packaging/ocr/paddlex/OCR.yaml`, from .27's file with its comments kept
  (`PP-OCRv5_server_rec` for Vietnamese diacritics, unwarping off,
  `max_num_input_imgs: 3`)
- Create: `packaging/ocr/paddlex/README.md`
- Create: `packaging/ocr/endpoints.example.json`: two entries, `paddle` (`max_pages: 3`,
  `languages: ["vie", "eng"]`) and `tesseract` (`languages: ["vie", "eng"]`), with header
  placeholders
- Create: `tests/unit/test_ocr_kits.py`
- Modify: `packaging/docker/secrets/README.md`: mention the endpoints file

- [ ] **Step 1: Write failing tests** in `test_ocr_kits.py`:
  - `test_example_endpoints_file_loads`: `load_endpoints` on the example succeeds.
  - `test_paddle_max_pages_matches_kit`: the example's `paddle.max_pages` equals `OCR.yaml`'s
    `Serving.extra.max_num_input_imgs`. Read it with a regex, because there is no YAML
    dependency.
  - `test_kits_pin_images`: no `image:` line in either kit compose file ends in `:latest` or has
    no tag.
  - `test_main_compose_has_no_model_service`: the service names in
    `packaging/docker/compose.yml` are exactly `{api, worker, postgres, minio, createbucket}`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_ocr_kits.py -q`. Expected: FAIL.
- [ ] **Step 3: Write the kit files.** Fetch .27's two files over SSH, read-only, and strip any
  host-specific paths.
- [ ] **Step 4: Run the tests, then the full gates from Global Constraints.** Expected: all PASS.
- [ ] **Step 5: Commit** `feat(012): a PaddleX serving kit and an example endpoints file`

---

### Task 8 (manual, needs the user): replace the shim on .27

Not code. **Confirm with the user immediately before acting**, because mmerp calls the shim
directly today. Warn them first that the new server returns real HTTP statuses, does not
truncate, and adds `rec_scores`.

- [ ] **Step 1:** Show the user the exact commands (copy `packaging/ocr/tesseract/` to
  `~/tesseract-ocr-docdoc/`, `docker compose up -d --build` on port 8081 after stopping the old
  container), along with the rollback (`docker compose -f ~/tesseract-ocr/compose.yaml up -d`).
  Wait for their yes.
- [ ] **Step 2:** Run the commands, then re-run
  `uv run pytest -m provider tests/integration/test_paddlex_live.py` through the tunnel.
  Expected: PASS.
- [ ] **Step 3:** Report the result, including the nginx auth type if it is now known.
