# ADR-0021: A Request May Name a Configured Parser; OCR Endpoints Are Configured, Not Coded

- **Status**: Accepted; amended by [ADR-0022](0022-asynchronous-ocr-through-a-bounded-queue.md)
  (`POST /v1/ocr` now queues an OCR run instead of answering in the request)
- **Date**: 2026-10-19
- **Amends**: the constitution, Principle IV (Provider-Agnostic Adapters) and Principle V
  (Text-First Document Processing) — see the Sync Impact Report for v1.9.0 → v1.10.0
- **Relates to**: [ADR-0001](0001-parser-and-ocr-strategy-in-mvp.md) (`OCRProvider` deferred; this
  decision does not revisit that), [ADR-0012](0012-storeless-extraction-over-http.md) (the
  storeless shape the raw OCR route this design introduces follows)
- **Principles engaged**: IV (Provider-Agnostic Adapters), V (Text-First Document Processing),
  VIII (Reproducibility, Provenance, and Versioning — no silent fallback), XI (MVP Discipline)
- **Superseded usage**: the `docdoc parse` command named below was removed in [ADR-0027](0027-service-only-surface.md); naming a parser is an HTTP concern only.

## Context

docdoc is becoming an OCR gateway (`docs/superpowers/specs/2026-10-19-ocr-gateway-design.md`):
other systems — the first is mmerp — send it a document and want OCR back from a **specific**
engine they chose, not whichever one docdoc's capability selection would have picked. That is a
plain conflict with the constitution as written. Principle IV says: "Parsers are selected by
declared capabilities, never by hard-coded provider name." A caller naming `paddle` or `tesseract`
on a request is not docdoc hard-coding a provider — but the sentence does not distinguish the two,
and a design that resolves the conflict by reading the sentence narrowly would invert Governance's
precedence rule exactly as ADR-0019 and the v1.6.0/v1.8.0 amendments each record about themselves.
So this is an amendment, not an interpretation, and this ADR is where the amendment's new line is
spelled out concretely — the same role ADR-0019 played for the console.

A second, smaller conflict sits in Principle V. The preferred path for a text PDF is the native
parser; OCR is for documents that actually need it. A caller who names a recognition parser on a
document that *does* have a usable text layer is asking docdoc to take the OCR path anyway — on
purpose, because the caller may want OCR's specific output shape (line-level boxes, per-line
confidence) even when the native parser would have worked. Silently honouring that without
recording what the native rule would have said turns an inspectable routing decision into a guess
about why a text PDF went through OCR.

A third question, not a conflict but a design decision this ADR also has to record: the gateway
needs to reach the PaddleX serving protocol under *any number* of configured names (`paddle`,
`tesseract`, and whatever a deployment adds next), and docdoc must not grow a new adapter file
every time an operator stands up another PaddleX-protocol-compatible service.

## Decision

### 1. A request may name a configured parser. Code never does.

Principle IV's rule becomes: parsers are selected by declared capabilities, never by a provider
name hard-coded *in docdoc*. A request — `POST /v1/ocr?parser=...`, the `parser=` argument to
`ingest.parse` for an in-process caller — may name one of the parsers the deployment has
configured. There is no CLI flag: `docdoc parse` has no `--parser`, naming a parser is an HTTP or
library-level concern. **The request then fails rather than falling back**: an unknown
name, a name that is currently unavailable, or a name that cannot serve the document's media type
or the capabilities asked for raises `ParserCapabilityError` (HTTP 422) naming the parser and the
reason. It never silently substitutes a different parser for the one asked for.

The distinction that keeps this from reopening Principle IV's whole purpose is *who* holds the
name. The fastest way to turn an open-source engine into an unmaintainable vendor wrapper — the
rationale Principle IV gives for itself — is a codebase that imports a provider SDK and branches
on its name in application code. That failure mode lives in `if parser_id == "azure-di":` inside
docdoc's own source, where a reader has to open the file to learn what is possible. Nothing about
*that* changes: no such branch is introduced, azure-di and gcv's special-casing is already
confined to their adapters, and this decision adds no logic to docdoc that is specific to any one
parser's name. What changes is that a **caller-supplied string**, already compared only against a
registry's declared ids (`ParserRegistry.select`, `ingest/registry.py`), may now also decide
selection outright instead of only acting as a tie-break. With no name given, capability-based
selection runs exactly as it does today and remains the default a deployment gets without asking
for anything.

### 2. A named recognition parser on a text PDF is honoured, and the text-layer verdict survives as an override

Principle V gains: a request that explicitly names a configured recognition parser is honoured
even when the document's text layer is usable. docdoc does not second-guess a caller who asked for
OCR by name on the theory that the native path would have been better.

The text-layer rule still runs. Its verdict — usable or not — is still computed and recorded, as
`overridden`, by `TextLayerRule.as_override` (`src/docdoc/ingest/assess.py`), the same mechanism
`force="recognition"` already uses today for the unnamed case. `overridden_verdict` keeps what the
rule actually said; `text_layer_usable` on the record becomes the route taken. The failure mode
this prevents is a routing decision that becomes unrecoverable the moment a caller overrides it: a
document that went through OCR for no inspectable reason, next to a sibling that went through OCR
because its text layer genuinely failed — indistinguishable in provenance, and therefore
unauditable exactly where Principle V requires the opposite. Naming a parser changes which route is
taken; it does not stop docdoc from recording what it would have decided on its own.

### 3. Endpoints speaking the PaddleX `/ocr` protocol are configured, not coded

The PaddleX serving protocol (`POST /ocr`, `result.ocrResults[i].prunedResult`) is implemented by
**one** adapter, confined to `docdoc.ingest.parsers.paddlex` per Principle IV's adapter-directory
rule. That adapter is parametrised by any number of configured endpoints — an id, a URL, optional
headers, declared media types, languages, a page limit, and optional per-attempt/per-parse
timeouts — read from a configuration file named by the `DOCDOC_OCR_ENDPOINTS_FILE` environment
variable, following the `DOCDOC_*_FILE` convention the gateway design already settles on. None of
this is a *capability* in `ParserCapabilities`' sense — only `media_types` (plus `requires_network`,
fixed for this adapter) is; `languages`, the page limit, and the timeouts are declared data this
adapter consults, not capabilities a caller's `require` can ask for. Standing up a third
PaddleX-protocol service, or pointing `tesseract`
at a different host, is a configuration change, never a new adapter file and never a new docdoc
release. This is what lets "the caller picks the provider" (the gateway design's Goal 2) scale past
two names without multiplying code: the alternative — one adapter per configured endpoint name — is
exactly the hard-coded-provider-name failure Principle IV exists to prevent, moved one layer down
but not solved.

No endpoint URL, header value, or credential appears in any error, log event, `repr`, or HTTP body
this adapter produces. Errors and the discovery route name an endpoint by its configured `id` only
— the same no-leak rule every other provider boundary in this project already follows, applied to a
boundary that is now data instead of code.

### 4. `OCRProvider` stays deferred; the adapter satisfies `Parser`

ADR-0001 is not reopened. The PaddleX-protocol adapter is a `Parser`: it consumes a blob (or a
caller-supplied language option) and returns a `Document`, which is the whole of what ADR-0001
asked a document-intelligence provider to do before introducing a second interface. Nothing about
letting a caller name an endpoint, or about splitting a long PDF into sub-PDFs before sending it,
requires a capability `Parser` cannot express — page limits and media types are already
`ParserCapabilities` fields, and the chunking the design calls for is internal to the adapter's
`parse()`, invisible to the contract. `OCRProvider` remains introduced only when a local OCR engine
is implemented and genuinely cannot be expressed as a `Parser` — unchanged from ADR-0001, and this
ADR records that the gateway work did not find that case.

### 5. docdoc runs no model; kits are separate compositions

Every OCR engine the gateway reaches is a separate HTTP service, reached through the configured
endpoint, never a model loaded into docdoc's own process. This is a restatement of the MVP Scope
Constraints' "Deferred technology" line — no standalone OCR engine runs *inside* docdoc — applied
to the new case of a deployment that wants PaddleOCR or Tesseract available at all: it stands up
docdoc's deployment kits (`packaging/ocr/paddlex/`, `packaging/ocr/tesseract/`), which are compose
files and, for Tesseract, a small HTTP server docdoc ships that speaks the PaddleX protocol — not a
fifth service in `packaging/docker/compose.yml` and not a model dependency in the base install. The
failure mode this prevents is the one the MVP scope constraint was already written against: a
`pip install docdoc` that silently pulls in a model runtime because one deployment path wanted OCR
nearby. The kits run on their own hosts, typically not even the one docdoc runs on, and
`packaging/ocr/tesseract/server/` imports no `docdoc` module — it is a deployable in its own right,
not a disguised extension of docdoc's process.

## Consequences

- **A caller can get an unexpected result on purpose, and that is now a documented capability, not
  a bug report.** Asking for `tesseract` on a clean digital PDF produces OCR's line-level boxes
  instead of the native parser's token-level ones. The override is recorded, so a result that looks
  worse than the native path would have produced is explainable from its own provenance rather than
  from a log nobody kept.
- **The registry's selection surface grows one input.** `ParserRegistry.select` (or its caller) now
  accepts an explicit id, which it did not need to validate against capabilities before — a named
  selection that contradicts the document's own media type or the parser's declared capabilities is
  exactly the shape `ParserCapabilityError` already exists to report, so no new error type is
  needed, only a new path that can raise the existing one.
- **The PaddleX-protocol adapter is now a plugin surface in effect, without being one in name.** The
  gateway design's own non-goal — "no third-party plugin mechanism" — holds: a new named parser is
  a new entry in a JSON file speaking a protocol docdoc already implements, not a new Python entry
  point. A deployment that needs a genuinely different protocol still needs a new adapter and,
  because Principle IV's directory rule is unchanged, that adapter still lives under
  `ingest/parsers/`.
- **`docdoc[ocr]` is a new optional extra**, carrying `pypdfium2` for the PDF splitting the
  endpoint page limit requires. The base install is unchanged; a deployment that never configures
  an OCR endpoint installs nothing new. (`pypdfium2`, not PyMuPDF, per the licensing constraint
  recorded in the gateway design.)
- **No migration is required.** No existing request names a parser today, so every call that
  omits a name keeps the exact selection behaviour it had before this amendment. Nothing previously
  compliant becomes non-compliant.

## Alternatives considered

**Leave Principle IV as written, and call endpoint selection "configuration" rather than
"provider name."** Rejected as the reinterpretation this ADR's Context section already argues
against: a caller choosing `paddle` over `tesseract` by name is choosing a provider by name under
any plain reading of the sentence, whatever the internal plumbing is called. Recording the
amendment openly is cheaper than defending a reading of the old text that a future contributor
would have to re-derive.

**Introduce `OCRProvider` now, since OCR is finally getting a real adapter.** Rejected. ADR-0001's
test was never "does OCR exist" — it was "do the `Parser` and a hypothetical `OCRProvider` contract
provably diverge." They still do not: the PaddleX adapter's input is a blob plus options and its
output is a `Document`, identical in shape to `azure-di` and `gcv`. Adding a second interface with
one implementation and no divergence to justify it is the exact violation Principle XI names:
"every abstraction MUST have a concrete, present-tense reason to exist."

**Let the caller pass a provider's own request shape through verbatim (a passthrough mode).**
Rejected by the gateway design itself (anti-goal: "becoming a vendor wrapper") and not revisited
here: the response and the request vocabulary stay docdoc-neutral regardless of which configured
parser served them, which is what lets a deployment swap `paddle` for another PaddleX-protocol
endpoint without mmerp noticing.
