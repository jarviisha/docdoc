# PaddleX, serving its own OCR pipeline

The official PaddleX serving CLI (`paddlex --serve`), answering the native
`/ocr` protocol -- `POST /ocr`, `result.ocrResults[i].prunedResult` -- that
`docdoc`'s `PaddleXParser` calls (`src/docdoc/ingest/parsers/paddlex.py`,
ADR-0021). `packaging/ocr/tesseract/` answers the same protocol from a
different engine; this kit is the protocol's own reference implementation.

`OCR.yaml` pins this deployment's own choices, each with its own comment:
`PP-OCRv5_server_rec` for Vietnamese diacritics a smaller model drops
entirely, unwarping off (a flat scan does not need it, and it costs RAM on a
full PDF page), and `max_num_input_imgs: 3` so a long PDF does not exhaust
the host's RAM.

**PaddleX silently truncates a PDF longer than `max_num_input_imgs`** -- HTTP
200, the first N pages, no error. That is why the endpoints file entry for
this kit must set `max_pages` to the *same* number as `max_num_input_imgs`
here: with it, `PaddleXParser` cuts a longer PDF into `max_pages`-sized
pieces itself and sends one request per piece, so the whole document still
parses; without it, docdoc has no reason to split, sends the whole PDF in one
request, and gets back a silently short document instead of an error.
`tests/unit/test_ocr_kits.py::test_paddle_max_pages_matches_kit` pins the two
numbers agreeing.

## Run it

```bash
docker compose -f packaging/ocr/paddlex/compose.yml up -d
curl -s localhost:8080/health
```

First boot installs the `serving` plugin into the image and downloads this
pipeline's models into `./models` (bind-mounted, so a restart does not repeat
either). Published on `127.0.0.1` only, like `packaging/ocr/tesseract/`'s own
deployment. There is no TLS and no authentication here -- front it with a
TLS- and auth-terminating reverse proxy before this is reachable from
anywhere but the host it runs on (`compose.yml`'s own comment).

## Configure it

There is no environment variable here, unlike `packaging/ocr/tesseract/`:
this kit's one configuration surface is `OCR.yaml` itself. Changing the
recognition model, the language, or `max_num_input_imgs` is an edit to that
file and a container restart -- and, for `max_num_input_imgs`, the matching
edit to the endpoints file described below.

## Register it in docdoc's endpoints file

`docdoc` finds a PaddleX-protocol endpoint through
`DOCDOC_OCR_ENDPOINTS_FILE`, a JSON file naming one or more of them
(`docs/concepts/ingest.md`). Add this server under its own `id`:

```json
{
  "endpoints": [
    {
      "id": "paddle",
      "url": "http://localhost:8080/ocr",
      "version": "3.3.11",
      "languages": ["vie", "eng"],
      "max_pages": 3
    }
  ]
}
```

`id` is what every error and provenance record names this endpoint by --
never its URL (ADR-0021 §3). `max_pages: 3` is not a guess: it is
`OCR.yaml`'s own `max_num_input_imgs`, copied -- see the warning above for
what disagreeing between the two would cost. `packaging/ocr/endpoints.example.json`
carries this same entry alongside `packaging/ocr/tesseract/`'s.
