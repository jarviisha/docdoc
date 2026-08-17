# Ingest: from a file to a Document

The ingest layer answers one question: *what does this file look like as a
[`Document`](document.md)?* It takes bytes and returns the canonical IR. It does
not persist, cache, extract, or ground.

## Two paths, one contract

| | Native text path | Recognition path |
|---|---|---|
| Parser id | `pdf-text` | `azure-di` |
| Extra | `docdoc[pdf]` | `docdoc[azure]` |
| For | PDFs with a usable text layer | Scans, photographs, anything without one |
| Needs | nothing — offline, free | credentials, network, money |
| Geometry | native, exact | recognized |

Both satisfy the same `Parser` protocol and produce documents of the same shape.
Downstream code cannot tell which one ran, except by reading provenance — which
is the point. Adding a third parser means implementing the protocol and
registering it; nothing in docdoc privileges either of the two that ship.

## The text-layer decision

Forcing OCR onto a PDF that already carries text destroys accurate native
geometry, multiplies cost and latency, and lowers extraction quality. So docdoc
decides which path a document takes *before* choosing a parser, using one
versioned rule — and records the decision.

**`text-layer@1`**

- A page is **text-bearing** when it yields at least **100** characters after
  discarding whitespace, control characters, and `U+FFFD`.
- A document's text layer is **usable** when at least **50%** of its pages are
  text-bearing, and at least one is.

Both thresholds are configurable. Changing a default requires a new rule id, so a
retune is visible in results produced before it.

The numbers come from the gap between two populations. On the committed
fixtures, text-bearing pages measure 242–307 characters and the page furniture on
a scan — a stamped page number — measures 8. The threshold sits in the empty
middle, not near either edge.

### It is recorded per page

```python
verdict = document.provenance.text_layer

verdict.rule_id             # 'text-layer@1'
verdict.text_layer_usable   # the document-level verdict, which decided routing
verdict.pages               # one PageTextVerdict per page: index, char_count, text_bearing
```

Routing is a whole-document decision — a document goes entirely to one path — but
the evidence is per page. That matters for the common case of a scanned page
appended to a digital contract: the document is parsed natively, that page
contributes no tokens, and the per-page verdict is what makes its emptiness a
recorded fact rather than a silent gap.

### Overriding it

```python
parse(data, force="recognition")
```

The rule still runs where it can, and its verdict is preserved in
`overridden_verdict`. Where it *cannot* run — a deployment that installed
`docdoc[azure]` but not `docdoc[pdf]`, so there is no reader to ask — an
unforced parse fails explicitly and a forced one succeeds with `rule_not_run`
recorded. docdoc will not guess at an answer it could not compute.

## Choosing a parser

```python
from docdoc.ingest import CapabilityRequest, parse

parse(data, require=CapabilityRequest(media_type="application/pdf", geometry=True))
```

You ask for capabilities. A deployment's configured priority decides which parser
supplies them, defaulting to offline before service-backed, with the parser id as
the final tie-break. Registration order and dictionary iteration never influence
the outcome.

The `media_type` in a request must agree with the bytes. The bytes are what decide
what a file is, so a request naming `image/png` for a PDF cannot be satisfied as
asked — and quietly answering a different question is the habit this layer rejects
everywhere else. It raises `UnsupportedDocumentError(reason="mime_type")` naming
both types. A caller with no opinion omits `require` and gets text plus geometry
for whatever the bytes turn out to be.

When nothing available satisfies the request, the error names the capability and
lists every candidate with its availability and reason — so "you have no
credentials configured" never arrives disguised as "no parser can do that".

There is **no fallback**. A failed parse surfaces; it does not quietly become
another parser's problem, because a scan silently retried on the native reader
would produce a near-empty document that looks like a success.

## What each adapter is responsible for

Reading order and coordinate systems belong to the adapter, not to the core. Each
declares what it does:

- `pdf-text` declares `pymupdf-stream@1` — the PDF content-stream order. It is
  **not** a layout reconstruction: PyMuPDF's sorted mode was measured and found
  to sort by vertical position across the whole page, interleaving columns, so it
  is not used. docdoc performs no layout analysis and claims none.
- `azure-di` declares `azure-di-service@1` — the service's own ordering.

Geometry is normalized in the adapter: `0..1`, top-left origin, one page per box.
No provider's coordinate system reaches a `Document`. A box sitting up to 1%
outside the page is treated as rendering slop and clamped; anything further out
raises, because that is a coordinate-system bug and a wrong box is worse than a
missing one.

`pdf-text` maps every box through the page's rotation matrix first: PyMuPDF
reports word boxes in *unrotated* space while the page size is the displayed one,
so skipping that step would misplace every box on a rotated page.

## Provenance and identity

Every document records the parser, its version, the options, the declared
capabilities, the reading order, and the text-layer verdict.

`parser_version` embeds the underlying library or service version —
`1.0.0+pymupdf-1.28.2` — because a library upgrade that changes extraction must
change document identity. `document_id` derives from the blob, the parser, that
version, and the options hash, so two parses of one file are never
interchangeable.

Retry, timeout, and deadline settings are deliberately a *separate* type from
parse options. They cannot change the content of a successful result, so they
must not be able to change its identity.

## Failure

| Error | When |
|---|---|
| `UnsupportedDocumentError` | unrecognized or disallowed type, over the size or page limit, encrypted, corrupt |
| `ParserCapabilityError` | no available parser satisfies the request |
| `ParserError` | a parser produced something that cannot become a valid Document |
| `ProviderError` | a service-backed parse failed |

No provider SDK exception ever escapes an adapter; each is translated with the
original attached as `__cause__`. Only transient provider failures are retried —
at most three attempts, exponential backoff with jitter, bounded by a per-attempt
timeout *and* an overall deadline. A rejected credential fails on the first
attempt, because trying again cannot change the answer.

When the service says how long to wait, that interval wins over docdoc's own
backoff and is treated as a **floor**: jitter may extend it, never shorten it.
Coming back early to a service that has just rate-limited you is how the next
rate-limit is earned. docdoc's own backoff is still jittered in both directions,
which is the usual defence against a fleet of clients retrying in lockstep — that
reasoning applies to an interval docdoc invented, not to one the server chose.

The deadline overrides both. A service asking for longer than the remaining
budget does not get it; the parse fails on the deadline rather than sleeping
past it, and no partial wait happens first.

## Observability

One structured `ingest.parse` event per parse, success or failure, carrying
identifiers, the parser, the verdict, page count, duration, attempts, and
outcome. Identifiers and numbers only — never document text, never a credential.
