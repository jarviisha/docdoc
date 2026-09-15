# Seeing where a value came from

The shortest path from a PDF to a rectangle on a page, with no store, no database and no credentials.

Principle XII requires every feature to ship with a runnable example; this is grounding's.

**There used to be a browser in this file.** A read-only viewer at `/ui` rendered the page and drew
the boxes; it was retired by [ADR-0020](../docs/adr/0020-retire-the-grounding-viewer.md) because it
could not work on a deployment with authentication enabled. The route it called —
`POST /v1/extract` — is unchanged, and it is what the rest of this page uses. The rectangles are in
the response; nothing draws them for you.

## Run it

```bash
pip install 'docdoc[api,pdf]'

export DOCDOC_SCHEMA_PATHS="$PWD/schemas"
export DOCDOC_MODEL_ADAPTERS=echo                        # answers offline, costs nothing
export DOCDOC_ECHO_FIXTURES="$PWD/tests/fixtures/echo"   # and the answers it gives
# DOCDOC_STORE_ROOT deliberately unset — see below

uvicorn --factory docdoc.api.app:create_app --host 127.0.0.1 --port 8000
```

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v1/extract?schema=invoice@1' \
  --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
  | jq '{counts: .grounding.counts, verdict}'
```

```json
{
  "counts": { "exact": 5, "fuzzy": 0, "ungrounded": 8, "not_applicable": 2, "truncated": 0 },
  "verdict": "valid"
}
```

## What you should see, and why it is not a failure

**15 values.** Thirteen the model asserted and two it reported absent. Of the thirteen, **five carry
rectangles and eight do not**.

That is the correct output. The eight are real extracted values the grounder could not locate — a
model that read `1240.00` from a page rendering `1,240.00`, and so on — and Principle II forbids
emitting a location for a value that was not found. Anything that drew thirteen rectangles would be
lying about eight of them, and anything that listed only the five would be hiding them.

**One of the five carries two rectangles.** `line_items[0].description` wraps across two lines, so
the run returns two boxes. Worth knowing if you render these yourself: drawing only the first is a
wrong answer that looks like a right one.

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v1/extract?schema=invoice@1' \
  --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
  | jq '.grounding.fields["line_items[0].description"]'
```

Boxes arrive normalized to `0..1` with a top-left origin — already the coordinate system of a CSS box,
so a renderer multiplies by 100 and does nothing else. In particular it must **never** read
`Page.rotation`: the parsers resolve rotation before normalizing, so a box that reaches you is
already oriented the way the page is shown. Rotating to match double-rotates, and it fails *only* on
rotated pages — the plausible wrong implementation that looks correct everywhere you are likely to
test it.

## The same thing without HTTP

```bash
docdoc run tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1
```

## Why no store was configured

Note `DOCDOC_STORE_ROOT` was never set. Before Milestone 8 this request could not be made at all:
every route capable of extracting took a `blob_id`, a `blob_id` existed only after a submission, and
submission was refused without a store — so the document had to come to rest on disk before anything
could read it. `POST /v1/extract` takes the bytes and writes nothing (ADR-0012).

It also returns no `job_id`, because a run that writes no terminal artifact has no identity to hand
back. If you want one, submit the document first and use `POST /v1/documents/{blob_id}/extract`.

## Before you expose this

**By default it is unauthenticated**, and anyone who can reach it can spend your provider budget one
extraction at a time. Set `DOCDOC_API_KEYS_FILE` and every route but the two health probes requires a
credential — which is a fix rather than the end of it, now that no browser depends on being able to
reach `/v1/extract` without one.

**A run continues after the client disconnects.** There is no cancel on this route, because closing a
connection stops the waiting and not the work: the extraction runs to completion and the provider is
paid either way. A proxy in front of it must allow a request duration at least as long as your
slowest extraction.

See [`docs/concepts/grounding.md`](../docs/concepts/grounding.md) for the three states a value can be
in, and [`docs/concepts/console.md`](../docs/concepts/console.md) for the browser surface docdoc does
ship — which is about the deployment, not about a document.
