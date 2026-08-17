# Contract: `docdoc.ingest` Public API

**Feature**: `002-ingest-parser-layer` | **Date**: 2026-08-17

Everything exported from `docdoc.ingest` is public and covered by this contract. Anything else is
private and may change without notice — the same rule `docdoc.kernel` follows.

The layer takes bytes and returns a `kernel.Document`. It never persists, never caches, never
extracts, and never grounds.

---

## 1. Entry point

```python
def parse(
    source: SourceFile | bytes,
    *,
    require: CapabilityRequest | None = None,
    options: Mapping[str, Any] | None = None,
    transport: TransportSettings | None = None,
    registry: ParserRegistry | None = None,
    force: Literal["native", "recognition"] | None = None,
    limits: Limits | None = None,
    rule: TextLayerRule | None = None,
) -> Document
```

The whole feature in one call: detect the type, enforce limits, assess the text layer, select a
parser by capability, parse, validate, and return.

| Guarantee | |
|---|---|
| Returns | A `Document` satisfying every kernel invariant, with `provenance.text_layer` and `provenance.reading_order` populated. |
| Raises | `UnsupportedDocumentError`, `ParserCapabilityError`, `ParserError`, `ProviderError` — never a provider SDK exception. |
| `force` | Bypasses routing. When the rule can run it still runs, and its verdict is preserved in `text_layer.overridden_verdict`; when it cannot run — no native reader installed — it is skipped, `overridden_verdict` is `None`, and `text_layer.rule_not_run` records why. |
| Never | Falls back to another parser after a failure. Returns a partial document. Logs content. |

Passing raw `bytes` is shorthand for `SourceFile.from_bytes(data)`.

`rule` was added during implementation. FR-010 requires both thresholds to be configurable, and
`parse()` is the only entry point — without it the rule could only be reconfigured by calling
`assess_text_layer` directly and bypassing `parse()` entirely.

---

## 2. Source

```python
class SourceFile:
    @classmethod
    def from_bytes(
        cls, data: bytes, *, declared_media_type: str | None = None, filename: str | None = None
    ) -> SourceFile: ...

    data: bytes
    media_type: str            # detected from the byte signature — authoritative
    declared_media_type: str | None   # recorded, never trusted
    filename: str | None
    blob_id: str               # "sha256:..."
    size_bytes: int
    def blob_ref(self) -> BlobRef: ...
```

`from_bytes` raises `UnsupportedDocumentError(reason="mime_type")` for an unrecognized signature.

```python
class Limits:
    max_size_bytes: int = 50 * 1024 * 1024
    max_pages: int = 1000
    allowed_media_types: frozenset[str] = frozenset({
        "application/pdf", "image/jpeg", "image/png",
    })
```

Size and media type are enforced before any parse or transmission — for **both** input forms.
`SourceFile.from_bytes` applies them at construction, and `parse()` applies them again to whatever it
is given, because a caller may hand in a `SourceFile` built earlier under different limits. The check
is idempotent; the alternative was a branch where enforcement could be forgotten. Defaults are a
starting point, tunable per deployment.

The page limit is checked as soon as the count is known, which is not always the same moment. Normally
the text-layer assessment has already counted the pages, so the check precedes the parse. When the
assessment was skipped — a forced path in a deployment with no native reader — the count first exists
in the parser's output, and the check runs there instead. That still stops an over-limit document from
becoming a `Document`; it cannot undo a transmission already made, which is why the size limit is the
one that bounds cost.

`image/tiff` is deliberately absent. Multi-page TIFF is common, and supporting it would require the
page-splitting semantics this milestone puts out of scope. A deployment that needs it adds the type
and accepts that only its first page is read — that is a decision to take explicitly, not a default.

---

## 3. Text-layer assessment

```python
def assess_text_layer(source: SourceFile, *, rule: TextLayerRule | None = None) -> TextLayerAssessment
```

Runs before parser selection and touches no network. Deterministic: identical bytes yield an identical
assessment, character counts included.

```python
class TextLayerRule:
    id: str = "text-layer@1"
    min_chars_per_page: int = 100
    min_text_bearing_fraction: float = 0.5
```

Changing either default requires a new `id` (`text-layer@2`), so a retune is visible in results
produced before it.

Raises `ParserCapabilityError` when the source is a PDF and no native reader is installed — it does
not guess.

This is what `force` is for. A deployment that installs only the recognition extra parses PDFs by
asking for that path explicitly, which skips the assessment rather than inventing its result. Without
`force`, the same deployment gets the error above — an inability to assess is never silently resolved.

See [data-model.md §5](../data-model.md) for `TextLayerAssessment` and `PageTextVerdict`.

---

## 4. Capabilities and selection

```python
class CapabilityRequest:
    media_type: str
    text: bool = True
    geometry: bool = False
    tables: bool = False
    handwriting: bool = False

class ParserRegistry:
    def register(self, parser: Parser, *, available: bool = True, reason: str | None = None) -> None: ...
    def register_unavailable(
        self, parser_id: str, capabilities: ParserCapabilities, *, reason: str
    ) -> None: ...
    def select(self, require: CapabilityRequest) -> Parser: ...
    def candidates(self, require: CapabilityRequest) -> tuple[RegistryEntry, ...]: ...
    def candidates_all(self) -> tuple[RegistryEntry, ...]: ...

def default_registry(priority: Sequence[str] | None = None) -> ParserRegistry
```

`select` filters by media type and declared capabilities, orders by position in `priority`
(default `("pdf-text", "azure-di")` — offline before service-backed), and breaks remaining ties by
`parser_id`. Registration order never influences the outcome.

`register_unavailable` exists because FR-018 cannot otherwise be honoured: when an extra is not
installed there is no parser object to register, and a caller still needs to be told the difference
between "not installed" and "no such capability".

`select` raises `ParserCapabilityError` when nothing satisfies the request. The error carries the
required capabilities and every candidate's availability and reason — `candidates()` exposes the same
view without raising, for diagnostics.

**Naming a provider is never required.** `require=CapabilityRequest(media_type="application/pdf",
geometry=True)` is the supported way to ask; reaching for a parser by id is supported only for tests
and for a caller deliberately pinning one.

**A `media_type` that contradicts the bytes is an error**, not something to quietly correct. The bytes
decide what a file is (ING-1), so a request naming `image/png` for PDF bytes cannot be satisfied as
asked — and answering a different question without saying so is the habit this layer rejects
everywhere else. Raises `UnsupportedDocumentError(reason="mime_type")` naming both types. A caller
with no opinion omits `require` entirely and gets text-plus-geometry for whatever the bytes are.

---

## 5. Parser protocol

```python
class Parser(Protocol):
    # Read-only by declaration: an adapter fixes these at class definition, and
    # document identity depends on two of them. A plain class attribute satisfies
    # a read-only property, so implementations stay simple.
    @property
    def id(self) -> str: ...              # "pdf-text", "azure-di"
    @property
    def version(self) -> str: ...         # "1.0.0+pymupdf-1.28.2"
    @property
    def capabilities(self) -> ParserCapabilities: ...
    @property
    def reading_order(self) -> str: ...   # "pymupdf-stream@1"

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings,
        text_layer: TextLayerRecord | None = None,
    ) -> Document: ...
```

`text_layer` was added during implementation. The routing verdict is something only the caller of the
ingest layer knows — a parser cannot compute why it was chosen — and `Document` is immutable with
provenance inside it, so there is no later moment at which the verdict could be attached without
rebuilding the document.

A third-party parser satisfying this protocol is a first-class citizen: register it and it competes
on capability like any other. Implementers must satisfy the shared contract test
(`tests/contract/test_parser_contract.py`), which asserts ING-4, ING-7, ING-8, and ING-9 against any
parser handed to it.

**Two shipped adapters**

| | `pdf-text` | `azure-di` |
|---|---|---|
| Extra | `docdoc[pdf]` | `docdoc[azure]` |
| Capabilities | text, geometry | text, geometry, tables, handwriting |
| Network | no | yes |
| Media types | `application/pdf` | PDF, JPEG, PNG |
| Reading order | `pymupdf-stream@1` | `azure-di-service@1` |

---

## 6. Transport

```python
class TransportSettings:
    max_attempts: int = 3
    initial_backoff_s: float = 0.5
    max_backoff_s: float = 8.0
    jitter: bool = True
    attempt_timeout_s: float = 30.0
    deadline_s: float = 120.0
```

Applies to service-backed parsers only; the native path ignores it.

A service-supplied wait interval takes precedence over `initial_backoff_s`, and is treated as a
**floor**: `jitter` may extend it, never shorten it. docdoc's own backoff is jittered in both
directions, which is the usual defence against clients retrying in lockstep; that reasoning does not
transfer to an interval the server chose, and coming back early to a service that has just
rate-limited you is how the next one is earned.

The deadline overrides both. A service asking for longer than the remaining budget does not get it —
the parse fails on the deadline rather than sleeping past it, and no partial wait happens first.

**These settings never reach `options_hash`.** Two parses differing only here yield the same
`document_id` (ING-5).

---

## 7. Errors

```text
DocdocError
├── KernelError            (Milestone 1)
│   └── CapabilityError    "this document cannot answer that — its parser supplied no geometry"
└── IngestError
    ├── UnsupportedDocumentError   reason: mime_type | size_limit | page_limit | encrypted | corrupt
    ├── ParserCapabilityError      "no available parser can satisfy this request"
    ├── ParserError                reason: invalid_order | capability_mismatch | empty_result | internal
    └── ProviderError              reason: timeout | deadline | rate_limit | auth | transport | service
```

`kernel.CapabilityError` and `ingest.ParserCapabilityError` are different questions and are never
interchangeable — see [research.md R10](../research.md).

Every ingest error carries `blob_id` and, where one was chosen, `parser_id`. None carries document
content. Provider exceptions are translated with the original attached as `__cause__`.

---

## 8. Observability

One structured event per parse, on both success and failure, via stdlib `logging` under the
`docdoc.ingest` logger. No formatting is imposed; the application chooses JSON or otherwise.

| Field | Example |
|---|---|
| `event` | `ingest.parse` |
| `blob_id` | `sha256:9f2c…` |
| `document_id` | `sha256:41ab…` (absent on failure) |
| `parser_id` / `parser_version` | `azure-di` / `1.0.0+azure-di-2024-11-30` |
| `media_type` | `application/pdf` |
| `text_layer_usable` / `text_layer_rule` | `false` / `text-layer@1` |
| `pages` | `12` |
| `duration_ms` | `840` |
| `attempts` | `2` |
| `outcome` | `ok` \| `error` |
| `error_type` / `error_reason` | `ProviderError` / `rate_limit` |

**Forbidden in this event, and everywhere else in the layer**: document text, token text, extracted
values, credentials, endpoint secrets, filenames of end-user documents. Identifiers, hashes, counts,
and timings only (FR-029).

Counters, histograms, and tracing are Milestone 7.

---

## 9. Stability

Pre-1.0, this surface may change; `TODO(PRE_1_0_VERSIONING)` in the constitution governs what is
promised. Within the milestone, three properties are load-bearing for everything above and change only
with a documented reason:

1. `parse()` returns a `Document` or raises a typed docdoc error — never both, never neither.
2. Capability selection never requires a provider name.
3. Transport settings never influence document identity.
