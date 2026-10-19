# Tesseract, speaking the PaddleX `/ocr` protocol

A Tesseract server that answers the same wire protocol the official PaddleX
serving CLI does -- `POST /ocr`, `result.ocrResults[i].prunedResult` -- so
`docdoc`'s `PaddleXParser` can call it exactly like PaddleX
(`src/docdoc/ingest/parsers/paddlex.py`, ADR-0021).

It replaces a hand-written shim that ran directly on the OCR host and that
mmerp still calls by URL, with no docdoc in between. This server preserves
that caller's behaviour -- including `visualize`'s `inputImage` preview --
while dropping the shim's defects: it reads every page of a document rather
than silently stopping at three, it never answers `200` with an error
hiding inside the body, and it reports a confidence per line.

This directory is a separate deployable. Nothing under `server/` imports
`docdoc`; the only thing it owes docdoc is the response shape
`docdoc.ingest.parsers.paddlex.pages_of` already reads.

## Run it

```bash
docker compose -f packaging/ocr/tesseract/compose.yml up -d --build
curl -s localhost:8081/health
```

Published on `127.0.0.1` only, like .27's own deployment. There is no TLS
and no authentication here -- front it with a TLS- and auth-terminating
reverse proxy before this is reachable from anywhere but the host it runs
on (`compose.yml`'s own comment).

## Configure it

Two environment variables, both read once at process start:

| Variable | Default | Meaning |
|---|---|---|
| `TESSERACT_LANGS` | `vie+eng` | Passed to `tesseract -l ...`. Both languages the shim's host ever served; narrow it or add a third (and the matching `tesseract-ocr-<code>` package to the `Dockerfile`) as needed. |
| `MAX_PAGES` | unset (no cap) | A document longer than this gets a `413`, not a truncated read. Unset means every page is always rendered and recognized, however many there are. |

## Register it in docdoc's endpoints file

`docdoc` finds a PaddleX-protocol endpoint through
`DOCDOC_OCR_ENDPOINTS_FILE`, a JSON file naming one or more of them
(`docs/concepts/ingest.md`). Add this server under its own `id`:

```json
{
  "endpoints": [
    {
      "id": "tesseract",
      "url": "http://localhost:8081/ocr",
      "version": "1.0.0"
    }
  ]
}
```

`id` is what every error and provenance record names this endpoint by --
never its URL (ADR-0021 §3), so it is worth choosing something that reads
well in a log line. Point `DOCDOC_OCR_ENDPOINTS_FILE` at the file and the
endpoint is available under that `id`, with no code change and no restart
of this server required.
