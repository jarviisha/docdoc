# Phase 1 Data Model: Ingest Parser Layer

**Feature**: `002-ingest-parser-layer` | **Date**: 2026-08-17

Entities the ingest layer introduces, the invariants that make an invalid one unconstructable, and
the one additive change to a Milestone 1 kernel type. Invariants carry stable `ING-n` identifiers so
tests and errors can name them rather than matching on message text — the same convention
`DOC-1…DOC-10` follows in Milestone 1.

Everything here is a frozen Pydantic model or a `NamedTuple`, mirroring the kernel's split: hot,
per-item values are tuples; aggregates validated once are models.

---

## 1. SourceFile

What a caller hands to the ingest layer. Bytes plus what the caller *claims*, kept separate from what
the bytes actually are.

| Field | Type | Notes |
|---|---|---|
| `data` | `bytes` | The file itself. Never stored in a `Document`. |
| `declared_media_type` | `str \| None` | What the caller said. Recorded, never trusted. |
| `media_type` | `str` | Detected from the byte signature (R9). This is the one that decides routing. |
| `filename` | `str \| None` | Advisory only; carried into `BlobRef`. |
| `blob_id` | `str` | `sha256:…`, from `kernel.blob_id_for(data)`. |

**Invariants**

- **ING-1** — `media_type` is derived from the byte signature, never from `declared_media_type` or
  the filename extension. An unrecognized signature raises `UnsupportedDocumentError(reason="mime_type")`.
- **ING-2** — `size_bytes` and the media type are checked *before any parse or transmission*. The page
  count is checked as soon as it is known, which is two different moments: before the parse when the
  text-layer rule ran and counted the pages, and immediately after it when the rule was skipped and the
  count did not exist until the parser produced one. Over-limit raises
  `UnsupportedDocumentError(reason="size_limit"|"page_limit")`. The later check still keeps an
  over-limit document from becoming a `Document`, but it cannot undo a transmission a remote parse has
  already made — the size limit is the one that bounds cost.
- **ING-3** — `blob_id` is a pure function of `data`. Two `SourceFile`s over identical bytes are
  identical in identity regardless of filename or declared type.

---

## 2. ParserCapabilities

The vocabulary callers use instead of provider names.

| Field | Type | Notes |
|---|---|---|
| `text` / `geometry` / `tables` / `handwriting` | `bool` | Mirrors `kernel.Capabilities` exactly. |
| `media_types` | `frozenset[str]` | What the parser accepts. |
| `requires_network` | `bool` | Drives the offline-first default priority (R11). |

**Invariants**

- **ING-4** — A parser's declared capabilities are compared against what it actually produced. A
  parser declaring `geometry=True` that returns any token without geometry raises `ParserError`; so
  does one declaring `geometry=False` that returns geometry. The kernel's all-or-nothing geometry rule
  (DOC-8) makes the partial case unconstructable anyway; ING-4 catches the mismatch earlier and blames
  the parser by name.

---

## 3. ParseOptions and TransportSettings

Deliberately two types, because exactly one of them may influence identity.

**`ParseOptions`** — a JSON-encodable mapping of knobs that can change the *content* of a result.
Reduced by `kernel.options_hash_for` and fed into `document_id_for`.

**`TransportSettings`** — attempt limit, backoff base, jitter, per-attempt timeout, overall deadline,
endpoint, credential source.

**Invariants**

- **ING-5** — `TransportSettings` never reaches `options_hash`. Two parses differing only in transport
  settings produce the same `document_id` (FR-039, SC-018).
- **ING-6** — `ParseOptions` must be canonically encodable; key ordering is irrelevant to the hash,
  which `kernel.canonical_json` already guarantees.

---

## 4. Parser (protocol)

| Member | Type | Notes |
|---|---|---|
| `id` | `str` | Stable, provider-neutral: `pdf-text`, `azure-di`. |
| `version` | `str` | Adapter version **plus** library/service version — `1.0.0+pymupdf-1.28.2`. |
| `capabilities` | `ParserCapabilities` | |
| `reading_order` | `str` | Declared ordering identity — `pymupdf-stream@1` as built (R5). |
| `parse(source, options, transport, text_layer)` | `→ Document` | Exactly one `Document` or an exception. |

**Invariants**

- **ING-7** — `parse` returns a fully valid `Document` or raises. No partial document, and no mutation
  of anything that already exists (FR-031).
- **ING-8** — Tokens arrive ascending and non-overlapping in text order. The ingest layer validates and
  raises `ParserError`; it never sorts, clips, or otherwise repairs (FR-037).
- **ING-9** — `version` changes whenever output changes for unchanged inputs, which the embedded
  library version makes automatic for the common case (ADR-0003).
- **ING-23** — The returned `Document` must belong to the file that was handed over: its
  `source.blob_id` is the input's, its provenance names the parser and version that actually ran, and
  its `text_layer` is the verdict it was routed with. Nothing else in the layer establishes this, and
  the `Parser` protocol invites third-party implementations, so it is checked rather than trusted —
  a document of the wrong file would leave every span pointing into a stranger's document while
  `document_id` certified it (FR-002, ADR-0002).

---

## 5. TextLayerAssessment

The verdict, its evidence, and the rule that produced it.

| Field | Type | Notes |
|---|---|---|
| `rule_id` | `str` | `text-layer@1` (R3). |
| `min_chars_per_page` | `int` | Default 100; part of the evidence, not just config. |
| `min_text_bearing_fraction` | `float` | Default 0.5. |
| `pages` | `tuple[PageTextVerdict, ...]` | One per page, in page order. |
| `text_layer_usable` | `bool` | The document-level verdict that decides routing. |
| `overridden` | `bool` | Whether a caller forced a path. |
| `overridden_verdict` | `bool \| None` | The verdict that was overridden, when one was. |
| `rule_not_run` | `str \| None` | Why the rule was skipped, when it was — e.g. `reader_unavailable`. |

**`PageTextVerdict`** (`NamedTuple`): `page_index: int`, `char_count: int`, `text_bearing: bool`.

**Implemented as one type, not two.** `ingest.TextLayerAssessment` is an alias for the kernel's
`TextLayerRecord` (§7) rather than a separate class carrying the same fields. Two identical types
would have no present-tense reason to exist (Principle XI), and are exactly how a verdict and its
copy drift apart. The kernel name is the definition; the ingest name is what the contract calls it.

**Invariants**

- **ING-10** — `text_layer_usable` is a pure function of `pages` and the two thresholds, except when
  `overridden` is true — in which case `overridden_verdict` holds what the rule said, so the rule's
  output is never lost. When the rule could not run, `overridden_verdict` is `None`, `rule_not_run`
  gives the reason, and `pages` is empty (FR-012).
- **ING-11** — Whenever the rule ran, `pages` has exactly one entry per page of the source. A per-page
  verdict is not optional; SC-003 measures its presence at 100%. The single exception is a skipped
  rule (ING-10), where `rule_not_run` explains the emptiness rather than leaving it unaccounted for.
- **ING-12** — The assessment is deterministic: identical bytes yield an identical assessment,
  including character counts (FR-010).
- **ING-13** — An image source yields `pages` of length 1, `char_count = 0`, `text_layer_usable =
  False`, without inspecting content (R4).

---

## 6. ParserRegistry

| Field | Type | Notes |
|---|---|---|
| `entries` | `Mapping[str, RegistryEntry]` | Keyed by `parser_id`. |
| `priority` | `tuple[str, ...]` | Default `("pdf-text", "azure-di")`. |

**`RegistryEntry`**: `parser`, `available: bool`, `unavailable_reason: str | None`.

**Invariants**

- **ING-14** — Selection is deterministic: filter by capability and media type, order by position in
  `priority`, break remaining ties by `parser_id`. Registration order and mapping iteration order never
  influence the result (FR-016, SC-011).
- **ING-15** — No satisfying parser raises `ParserCapabilityError` naming the required capabilities and
  listing every candidate with its availability and reason. A parser satisfying only part of the
  request is never substituted (FR-017).
- **ING-16** — An installed-but-unusable parser stays in the registry as `available=False` with a
  reason. It is never silently dropped (FR-018).
- **ING-17** — Selection never falls back after a failure. A parse that fails surfaces; a different
  parser runs only because a caller asked (FR-014).

---

## 7. Kernel change: `IngestProvenance` gains two fields

This is the one Milestone 1 type this feature modifies, and the change is purely additive. Both new
fields are plain data — no new kernel dependency, no I/O, no clock (Principle I holds).

```text
IngestProvenance
  parser_id, parser_version, options, options_hash, capabilities   # unchanged
  text_layer_used: bool                                            # unchanged — the summary
+ text_layer: TextLayerRecord | None = None                        # the full verdict + per-page evidence
+ reading_order: str | None = None                                 # the parser's declared ordering
```

`TextLayerRecord` is the kernel-side mirror of `TextLayerAssessment` (§5) and lives in
`kernel/provenance.py`, because the kernel may not import from `ingest`. The ingest layer builds one
from its assessment.

**Why extend the kernel rather than store this beside the document**: Principle I requires the
`Document` to carry its ingestion provenance, and FR-011 requires the verdict to be readable without
re-reading the source. A side-car record would satisfy neither — it would be separable from the
document it explains, which is exactly how provenance gets lost.

**Identity is unaffected.** `document_id = sha256(blob_id + parser_id + parser_version + options_hash)`
does not read provenance, so adding fields cannot change any existing document's identity. Both
fields default to `None`, so every `Document` constructed by Milestone 1 code and every existing test
remains valid.

**Invariants**

- **ING-18** — When `text_layer` is present, `text_layer.text_layer_usable` and the effective routing
  decision agree with `text_layer_used`; a contradiction is a construction error.
- **ING-19** — Every document produced by *this* feature sets both new fields. `None` remains legal
  only for hand-constructed documents, which is what keeps Milestone 1's tests and examples working.

---

## 8. Error model

All ingest errors descend from `IngestError(DocdocError)`, so `except DocdocError` still catches
everything docdoc raises. Names come from the constitution's fixed vocabulary (R10).

| Error | `reason` values | Raised when |
|---|---|---|
| `UnsupportedDocumentError` | `mime_type`, `size_limit`, `page_limit`, `encrypted`, `corrupt` | The file cannot be accepted at all. Never retried. |
| `ParserCapabilityError` | — | No available parser satisfies the request. Carries required capabilities and per-candidate availability. |
| `ParserError` | `invalid_order`, `capability_mismatch`, `empty_result`, `wrong_document`, `internal` | A parser produced something that cannot become a valid `Document`, or one that does not belong to the file it was given. |
| `ProviderError` | `timeout`, `deadline`, `rate_limit`, `auth`, `transport`, `service` | A service-backed parse failed. `auth` is permanent; `timeout`/`rate_limit`/`transport`/`service` are transient. |

`ProviderError` also carries `attempts` (how many were made) and `retry_after_s` (how long the
service asked the caller to wait, when it said so). The retry loop reads `retry_after_s` in preference
to its own backoff, and the deadline overrides both. It is a declared field rather than an attribute
attached on the way past, because an attribute that only sometimes exists is not something a retry
loop can depend on.

**Invariants**

- **ING-20** — No provider SDK exception escapes an adapter. Every one is translated, with the
  original attached as `__cause__` for debugging (FR-025).
- **ING-21** — Only transient `ProviderError` reasons are retried. `auth`, and every
  `UnsupportedDocumentError`, fail on the first attempt (FR-027, SC-017).
- **ING-22** — Every error carries enough of the input to identify it — `blob_id`, never content —
  and names the responsible parser *where one had been chosen* (FR-025, FR-029). A refusal that
  precedes parser selection, such as an unrecognized signature or an over-limit file, leaves
  `parser_id` unset: there was no responsible parser, and inventing one would be worse than reporting
  the absence.

---

## 9. Relationships

```text
SourceFile ──sniff/limits──▶ TextLayerAssessment ──verdict──▶ ParserRegistry.select(capabilities)
                                                                        │
                                                                        ▼
                                              Parser.parse(source, options, transport)
                                                                        │
                                                                        ▼
                                    kernel.Document {text, pages, tokens, blocks, tables,
                                                     source: BlobRef, provenance: IngestProvenance
                                                              └── text_layer, reading_order}
```

One structured log event (`ingest.parse`) is emitted per parse attempt outcome — schema in
[contracts/ingest-api.md](contracts/ingest-api.md).
