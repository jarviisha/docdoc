"""Record the fixtures Task 2's mapping tests pin against.

Run with ``uv run python tests/fixtures/paddlex/record.py`` against the two
PaddleX-protocol services tunneled from the OCR host (``192.168.100.27``) at
``http://127.0.0.1:18080/ocr`` (official PaddleX serving) and
``http://127.0.0.1:18081/ocr`` (the hand-written Tesseract shim). The outputs
are committed, so this script exists for reproducibility and review -- a
reviewer can see exactly what each fixture contains without re-running a live
OCR call -- not to run in CI, which has neither service reachable.

Only ``prunedResult.{rec_texts,rec_boxes,rec_scores,doc_preprocessor_res}``,
plus ``result.dataInfo``, ``errorCode``, and ``errorMsg`` are kept from each
response. Everything else -- most importantly the base64 image PaddleX echoes
back in some fields -- is dropped before anything is written, so no document
content or image bytes ever reach the repository.

**Observed truncation behaviour (max_num_input_imgs: 3), recorded 2026-10-19:**
Sending a 4-page PDF (built by concatenating ``scanned_contract.pdf`` and
``mixed_pages.pdf`` with pypdfium2) to the PaddleX endpoint returns **HTTP 200,
errorCode 0, errorMsg "Success"** -- PaddleX silently truncates to the first 3
pages rather than refusing the request. ``result.dataInfo.numPages`` reports
``3``, matching ``len(result.ocrResults)``, with no field anywhere in the
response naming the page count that was actually sent. A caller comparing
`numPages` against *itself* sees a perfectly consistent, perfectly truncated
result; only a caller who remembers how many pages it sent can tell the
difference. This is exactly the "silent partial result" the global
constraints forbid (ING-7) -- it means the PaddleX parser (Task 3) MUST track
the page count it sent per request and raise ``ProviderError(reason="service")``
itself when the response's page count is short, because the endpoint will
never say so on its own. The three-page ``mixed_pages.pdf`` sent alone (at
exactly the limit) returns all three pages untruncated, so the limit is
``> max_num_input_imgs``, not ``>=``.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
PDF_DIR = HERE.parent / "pdf"
IMAGE_DIR = HERE.parent / "image"

PADDLE_URL = "http://127.0.0.1:18080/ocr"
TESSERACT_URL = "http://127.0.0.1:18081/ocr"

#: PaddleX's own ``fileType`` codes: PDF bytes, or a raster image.
FILE_TYPE_PDF = 0
FILE_TYPE_IMAGE = 1

#: CPU OCR is slow (research.md: up to ~180s per request on the tunneled host).
REQUEST_TIMEOUT_S = 180


def _call(url: str, path: Path, file_type: int) -> dict[str, Any]:
    """POST one document and return the raw decoded JSON response.

    Read-only against the tunneled services: this never sends anything but an
    OCR request, and never retries -- a failure here means re-running the
    script, not silently masking a flaky recording.
    """
    payload = {
        "file": base64.b64encode(path.read_bytes()).decode("ascii"),
        "fileType": file_type,
        "visualize": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        return json.loads(response.read())


def _pruned(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """The handful of fields the mapping reads, dropped from everything else
    the service echoes back -- most importantly the base64 image it includes
    in some fields, which must never reach a committed fixture."""
    full = ocr_result["prunedResult"]
    kept = {"rec_texts": full["rec_texts"], "rec_boxes": full["rec_boxes"]}
    if "rec_scores" in full:
        kept["rec_scores"] = full["rec_scores"]
    if "doc_preprocessor_res" in full:
        kept["doc_preprocessor_res"] = full["doc_preprocessor_res"]
    return {"prunedResult": kept}


def _trimmed(raw: dict[str, Any]) -> dict[str, Any]:
    """The whole response, cut down to what Task 2's mapping consumes."""
    result = raw.get("result") or {}
    ocr_results = [_pruned(one) for one in result.get("ocrResults", [])]
    return {
        "errorCode": raw.get("errorCode"),
        "errorMsg": raw.get("errorMsg"),
        "result": {"ocrResults": ocr_results, "dataInfo": result.get("dataInfo")},
    }


def _record(name: str, url: str, path: Path, file_type: int) -> None:
    trimmed = _trimmed(_call(url, path, file_type))
    out = HERE / f"{name}.json"
    out.write_text(json.dumps(trimmed, indent=2) + "\n")
    print(f"recorded {out}")


def main() -> int:
    try:
        _record(
            "paddle.digital_invoice", PADDLE_URL, PDF_DIR / "digital_invoice.pdf", FILE_TYPE_PDF
        )
        _record(
            "paddle.scanned_contract", PADDLE_URL, PDF_DIR / "scanned_contract.pdf", FILE_TYPE_PDF
        )
        _record("paddle.rotated_90", PADDLE_URL, PDF_DIR / "rotated_90.pdf", FILE_TYPE_PDF)
        _record("paddle.sample_page", PADDLE_URL, IMAGE_DIR / "sample_page.png", FILE_TYPE_IMAGE)
        _record("paddle.mixed_pages", PADDLE_URL, PDF_DIR / "mixed_pages.pdf", FILE_TYPE_PDF)
        _record(
            "tesseract-shim.digital_invoice",
            TESSERACT_URL,
            PDF_DIR / "digital_invoice.pdf",
            FILE_TYPE_PDF,
        )
    except urllib.error.URLError as error:
        print(f"could not reach a recording endpoint: {error}", file=sys.stderr)
        return 1

    # paddle.blank_page.json is hand-made, not recorded: a copy of
    # paddle.scanned_contract.json with page 2's rec_texts/rec_boxes/rec_scores
    # emptied, to pin a blank page in the middle of a scan (Review Focus 2).
    # There is no live response to record -- PaddleX never emits an empty page
    # for either fixture PDF -- so this script does not attempt to reproduce it.
    print("paddle.blank_page.json is hand-made; see this file's docstring")
    return 0


if __name__ == "__main__":
    sys.exit(main())
