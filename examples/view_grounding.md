# Seeing where a value came from

The shortest path from a PDF to a rectangle on a page, with no store, no database and no credentials.

Principle XII requires every feature to ship with a runnable example; this is the viewer's.

## Run it

```bash
pip install 'docdoc[api,ui,pdf]'

export DOCDOC_SCHEMA_PATHS="$PWD/schemas"
export DOCDOC_MODEL_ADAPTERS=echo                        # answers offline, costs nothing
export DOCDOC_ECHO_FIXTURES="$PWD/tests/fixtures/echo"   # and the answers it gives
# DOCDOC_STORE_ROOT deliberately unset — see below

uvicorn --factory docdoc.api.app:create_app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/ui/>, pick `tests/fixtures/pdf/digital_invoice.pdf`, choose `invoice@1`,
and press Extract.

## What you should see, and why it is not a failure

**15 values listed.** Thirteen the model asserted and two it reported absent. Of the thirteen, **five
carry rectangles and eight do not**.

That is the correct output. The eight are real extracted values the grounder could not locate — a
model that read `1240.00` from a page rendering `1,240.00`, and so on — and Principle II forbids
emitting a location for a value that was not found. A viewer that drew thirteen rectangles would be
lying about eight of them, and one that listed only the five would be hiding them.

**One of the five carries two rectangles.** `line_items[0].description` wraps across two lines, so the
run returned two boxes and both are drawn. This is worth looking at: drawing only the first is a wrong
answer that looks like a right one, and it is what SC-001 is worded to catch.

Click a field and its rectangles are outlined; click a rectangle and its field is selected. The list
carries every fact the overlay does, so nothing is reachable only by looking at the picture.

## The same thing without a browser

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

Note `DOCDOC_STORE_ROOT` was never set. Before Milestone 8 this request could not be made at all: every
route capable of extracting took a `blob_id`, a `blob_id` existed only after a submission, and
submission was refused without a store — so the document had to come to rest on disk before anything
could read it. `POST /v1/extract` takes the bytes and writes nothing (ADR-0012).

It also returns no `job_id`, because a run that writes no terminal artifact has no identity to hand
back. If you want one, submit the document first and use `POST /v1/documents/{blob_id}/extract`.

## Two things to know before you expose this

It is **unauthenticated**, and anyone who can reach it can spend your provider budget one extraction at
a time. And a run **continues after the page is closed** — there is no cancel, because closing the
browser stops the waiting and not the work.

Both are covered in [How the viewer works](../docs/concepts/viewer.md), along with the parts of it that
carry no automated test.
