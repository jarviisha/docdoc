"""Unit tests for `packaging/ocr/tesseract/server/`: a separate deployable,
not part of the `docdoc` distribution the rest of this suite imports from an
installed package, so its two modules are loaded fresh, by path, instead
(task brief, Step 1).

`run_tesseract` is always monkeypatched here -- no `tesseract` binary is
needed to run these, only to run the real server.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

import pypdfium2 as pdfium
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parents[2] / "packaging" / "ocr" / "tesseract" / "server"

#: Real `tesseract ... tsv` output has more columns and more rows (a page-
#: level row, a block-level row, ...) than any test here needs -- only
#: `level == "5"` (word) rows are read, so that is all these fixtures carry.
TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)

#: One line of two close words ("Hello World"), then a row that is really
#: two columns -- "Foo" and "Bar" sit far enough apart on the same
#: tesseract line that the column split must cut them into two.
TWO_COLUMN_TSV = "\n".join(
    [
        TSV_HEADER,
        "5\t1\t1\t1\t1\t1\t10\t10\t30\t20\t90\tHello",
        "5\t1\t1\t1\t1\t2\t45\t10\t30\t20\t80\tWorld",
        "5\t1\t1\t1\t2\t1\t10\t50\t20\t20\t70\tFoo",
        "5\t1\t1\t1\t2\t2\t200\t50\t20\t20\t-1\tBar",
    ]
)

#: One line, three words: a confidence of -1 (tesseract's "none") and two
#: real ones, 80 and 60.
MIXED_CONFIDENCE_TSV = "\n".join(
    [
        TSV_HEADER,
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t-1\tA",
        "5\t1\t1\t1\t1\t2\t15\t0\t10\t10\t80\tB",
        "5\t1\t1\t1\t1\t3\t30\t0\t10\t10\t60\tC",
    ]
)

#: Every word confident and -1 at once: the "no confidence reported" case.
NO_CONFIDENCE_TSV = "\n".join(
    [
        TSV_HEADER,
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t-1\tOnly",
    ]
)


def _load(name: str) -> Any:
    """Load one server module fresh, by path. `app.py` does a plain
    `import ocr`; registering this module under that exact name in
    `sys.modules` first is what lets that resolve with no `sys.path` edit.
    """
    spec = importlib.util.spec_from_file_location(name, SERVER_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_app() -> Any:
    _load("ocr")
    return _load("app")


def _pdf_bytes(page_count: int) -> bytes:
    """A synthetic PDF with `page_count` blank pages -- content never
    matters here, since `run_tesseract` is always faked; only the page
    count does.
    """
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(page_count):
            document.new_page(200, 300)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
    finally:
        document.close()


class TestLinesFromTsv:
    def test_tsv_lines_and_boxes(self) -> None:
        ocr = _load("ocr")

        lines = ocr.lines_from_tsv(TWO_COLUMN_TSV)

        assert [text for text, _, _ in lines] == ["Hello World", "Foo", "Bar"]
        boxes = [box for _, box, _ in lines]
        assert boxes == [
            [10, 10, 75, 30],  # "Hello" (10,10,40,30) union "World" (45,10,75,30)
            [10, 50, 30, 70],
            [200, 50, 220, 70],
        ]

    def test_score_is_mean_conf_over_100_ignoring_minus_one(self) -> None:
        ocr = _load("ocr")

        mixed = ocr.lines_from_tsv(MIXED_CONFIDENCE_TSV)
        assert len(mixed) == 1
        assert mixed[0][2] == pytest.approx((80 + 60) / 2 / 100)

        unscored = ocr.lines_from_tsv(NO_CONFIDENCE_TSV)
        assert len(unscored) == 1
        assert unscored[0][2] is None


class TestOcrEndpoint:
    def test_all_pages_rendered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app()
        monkeypatch.setattr(app_module.ocr, "run_tesseract", lambda png, langs: TSV_HEADER)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(5)), "fileType": 0}
        response = client.post("/ocr", json=body)

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["dataInfo"]["numPages"] == 5
        assert len(result["ocrResults"]) == 5

    def test_visualize_adds_input_image_to_first_page_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Compatibility ruling: mmerp calls this server directly and may send
        # `visualize: true`, exactly as it did against the shim -- page 0 of
        # the response must carry an `inputImage` preview, and no other page.
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app()
        monkeypatch.setattr(app_module.ocr, "run_tesseract", lambda png, langs: TSV_HEADER)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(2)), "fileType": 0, "visualize": True}
        response = client.post("/ocr", json=body)

        assert response.status_code == 200
        results = response.json()["result"]["ocrResults"]
        assert "inputImage" in results[0]
        assert "inputImage" not in results[1]

    def test_max_pages_exceeded_is_413_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_PAGES", "2")
        app_module = _load_app()

        def _unexpected(png: bytes, langs: str) -> str:
            raise AssertionError("tesseract must not run on a document over MAX_PAGES")

        monkeypatch.setattr(app_module.ocr, "run_tesseract", _unexpected)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(5)), "fileType": 0}
        response = client.post("/ocr", json=body)

        assert response.status_code == 413
        assert response.json()["errorCode"] == 413

    def test_bad_base64_is_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app()

        client = TestClient(app_module.app)
        response = client.post("/ocr", json={"file": "not-valid-base64!!"})

        assert response.status_code == 400
        assert response.json()["errorCode"] == 400

    def test_tesseract_failure_is_500_with_paddlex_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app()

        def _boom(png: bytes, langs: str) -> str:
            raise TimeoutError("tesseract took too long")

        monkeypatch.setattr(app_module.ocr, "run_tesseract", _boom)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(1)), "fileType": 0}
        response = client.post("/ocr", json=body)

        assert response.status_code == 500
        payload = response.json()
        assert payload["errorCode"] == 500
        assert "logId" in payload
        assert "errorMsg" in payload


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
