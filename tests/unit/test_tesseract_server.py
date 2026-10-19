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
import types
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

import pypdfium2 as pdfium
from fastapi.testclient import TestClient
from PIL import Image

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


def _load(name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load one server module fresh, by path. `app.py` does a plain
    `import ocr`; registering this module under that exact name in
    `sys.modules` first is what lets that resolve with no `sys.path` edit.

    Goes through `monkeypatch.setitem` rather than assigning `sys.modules`
    directly, so the entry is removed again when the test ends instead of
    leaking a stale `ocr`/`app` module into whichever test runs next.
    """
    spec = importlib.util.spec_from_file_location(name, SERVER_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _load_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    # `ocr` first: `app.py`'s own `import ocr` must find the same module
    # object this test can still monkeypatch afterwards.
    _load("ocr", monkeypatch)
    return _load("app", monkeypatch)


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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class TestLinesFromTsv:
    def test_tsv_lines_and_boxes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ocr = _load("ocr", monkeypatch)

        lines = ocr.lines_from_tsv(TWO_COLUMN_TSV)

        assert [text for text, _, _ in lines] == ["Hello World", "Foo", "Bar"]
        boxes = [box for _, box, _ in lines]
        assert boxes == [
            [10, 10, 75, 30],  # "Hello" (10,10,40,30) union "World" (45,10,75,30)
            [10, 50, 30, 70],
            [200, 50, 220, 70],
        ]

    def test_score_is_mean_conf_over_100_ignoring_minus_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ocr = _load("ocr", monkeypatch)

        mixed = ocr.lines_from_tsv(MIXED_CONFIDENCE_TSV)
        assert len(mixed) == 1
        assert mixed[0][2] == pytest.approx((80 + 60) / 2 / 100)

        unscored = ocr.lines_from_tsv(NO_CONFIDENCE_TSV)
        assert len(unscored) == 1
        assert unscored[0][2] is None


class _FakeBitmap:
    def to_pil(self) -> Image.Image:
        return Image.new("RGB", (10, 10))


class _FakePage:
    def __init__(self, lock: Any) -> None:
        self._lock = lock

    def get_size(self) -> tuple[int, int]:
        return (100, 100)

    def render(self, scale: float) -> _FakeBitmap:
        # The point of the test this backs: rendering must only ever happen
        # with the module's pdfium lock held.
        assert self._lock.locked(), "pdfium render must run with the module lock held"
        return _FakeBitmap()


class _FakeDocument:
    def __init__(self, lock: Any, page_count: int) -> None:
        self._lock = lock
        self._page_count = page_count

    def __len__(self) -> int:
        return self._page_count

    def __getitem__(self, index: int) -> _FakePage:
        return _FakePage(self._lock)

    def close(self) -> None:
        pass


class TestPdfiumSafety:
    def test_pdfium_calls_are_serialized_by_a_module_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pypdfium2 is documented as unsafe to call from two threads at
        once (ocr.py's own comment on `_PDFIUM_LOCK`), and FastAPI runs the
        sync `/ocr` handler in a worker-thread pool -- so two concurrent
        requests are a real way to hit that. A live concurrency repro is
        exactly the kind of test that is flaky by nature (timing-dependent,
        and a real crash cannot be caught and asserted on); this instead
        fakes `pdfium.PdfDocument` and asserts, from inside the fake's own
        `render`, that the lock is held every time -- the guarantee that
        actually prevents the crash, checked directly rather than by trying
        to provoke the race.
        """
        ocr_module = _load("ocr", monkeypatch)
        fake_document = _FakeDocument(ocr_module._PDFIUM_LOCK, page_count=3)
        monkeypatch.setattr(ocr_module.pdfium, "PdfDocument", lambda data: fake_document)

        pages_iter, is_pdf_file = ocr_module.iter_pages(b"%PDF-fake", 0)
        assert is_pdf_file is True
        rendered = list(pages_iter)

        assert len(rendered) == 3
        # Released once the generator is exhausted -- not held for the
        # whole document, only around each individual pdfium call.
        assert not ocr_module._PDFIUM_LOCK.locked()

    def test_pdf_pages_are_rendered_lazily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`iter_pages` must hand back something that renders one page at a
        time -- a `list` would mean the whole document is already in memory
        before the caller OCRs even the first page. A generator is the
        structural guarantee: by construction it has done no work beyond
        whatever the most recent `next()` asked for.
        """
        ocr_module = _load("ocr", monkeypatch)
        pages_iter, _ = ocr_module.iter_pages(_pdf_bytes(3), 0)
        assert isinstance(pages_iter, types.GeneratorType)


class TestOcrEndpoint:
    def test_all_pages_rendered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app(monkeypatch)
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
        app_module = _load_app(monkeypatch)
        monkeypatch.setattr(app_module.ocr, "run_tesseract", lambda png, langs: TSV_HEADER)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(2)), "fileType": 0, "visualize": True}
        response = client.post("/ocr", json=body)

        assert response.status_code == 200
        results = response.json()["result"]["ocrResults"]
        assert "inputImage" in results[0]
        assert "inputImage" not in results[1]

    def test_tesseract_langs_env_reaches_run_tesseract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        monkeypatch.setenv("TESSERACT_LANGS", "eng+jpn")
        app_module = _load_app(monkeypatch)

        seen_langs = []

        def _fake_run_tesseract(png: bytes, langs: str) -> str:
            seen_langs.append(langs)
            return TSV_HEADER

        monkeypatch.setattr(app_module.ocr, "run_tesseract", _fake_run_tesseract)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(1)), "fileType": 0}
        response = client.post("/ocr", json=body)

        assert response.status_code == 200
        assert seen_langs == ["eng+jpn"]

    def test_max_pages_exceeded_is_413_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_PAGES", "2")
        app_module = _load_app(monkeypatch)

        def _must_not_run(*args: object, **kwargs: object) -> Any:
            raise AssertionError("no page may be rendered when the document is over MAX_PAGES")

        # Both patched: a real `iter_pages` call proves rendering was
        # attempted, and a real `run_tesseract` call proves OCR was -- the
        # 413 must come from the page-count check alone, before either.
        monkeypatch.setattr(app_module.ocr, "iter_pages", _must_not_run)
        monkeypatch.setattr(app_module.ocr, "run_tesseract", _must_not_run)

        client = TestClient(app_module.app)
        body = {"file": _b64(_pdf_bytes(5)), "fileType": 0}
        response = client.post("/ocr", json=body)

        assert response.status_code == 413
        assert response.json()["errorCode"] == 413

    def test_bad_base64_is_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app(monkeypatch)

        client = TestClient(app_module.app)
        response = client.post("/ocr", json={"file": "not-valid-base64!!"})

        assert response.status_code == 400
        assert response.json()["errorCode"] == 400

    def test_unreadable_file_is_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Valid base64, but the decoded bytes are neither a PDF nor an image
        # tesseract never runs; this must not read as a tesseract failure.
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app(monkeypatch)

        def _must_not_run(*args: object, **kwargs: object) -> Any:
            raise AssertionError("tesseract must not run on a file that could not be decoded")

        monkeypatch.setattr(app_module.ocr, "run_tesseract", _must_not_run)

        client = TestClient(app_module.app)
        response = client.post("/ocr", json={"file": _b64(b"not a real file at all")})

        assert response.status_code == 400
        assert response.json()["errorCode"] == 400

    def test_cmyk_jpeg_is_converted_before_tesseract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A valid image Pillow's PNG writer cannot save directly -- this
        # must OCR successfully, not 500.
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app(monkeypatch)
        monkeypatch.setattr(app_module.ocr, "run_tesseract", lambda png, langs: TSV_HEADER)

        cmyk_image = Image.new("CMYK", (20, 20))
        buffer = io.BytesIO()
        cmyk_image.save(buffer, "JPEG")

        client = TestClient(app_module.app)
        response = client.post("/ocr", json={"file": _b64(buffer.getvalue()), "fileType": 1})

        assert response.status_code == 200

    def test_multi_frame_tiff_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Only frame 0 would ever be OCR'd and reported as the whole image --
        # a silent partial result (ING-7) rather than a crash, which is why
        # this is refused outright instead of quietly OCR-ing one frame.
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app(monkeypatch)

        def _must_not_run(*args: object, **kwargs: object) -> Any:
            raise AssertionError("tesseract must not run on a multi-frame image")

        monkeypatch.setattr(app_module.ocr, "run_tesseract", _must_not_run)

        frame_one = Image.new("L", (20, 20), color=0)
        frame_two = Image.new("L", (20, 20), color=255)
        buffer = io.BytesIO()
        frame_one.save(buffer, "TIFF", save_all=True, append_images=[frame_two])

        client = TestClient(app_module.app)
        response = client.post("/ocr", json={"file": _b64(buffer.getvalue()), "fileType": 1})

        assert response.status_code == 400
        assert "frame" in response.json()["errorMsg"]

    def test_tesseract_failure_is_500_with_paddlex_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAX_PAGES", raising=False)
        app_module = _load_app(monkeypatch)

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


class TestMaxPagesStartupValidation:
    def test_max_pages_zero_is_refused_at_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_PAGES", "0")
        with pytest.raises(RuntimeError, match="MAX_PAGES"):
            _load_app(monkeypatch)

    def test_max_pages_negative_is_refused_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAX_PAGES", "-1")
        with pytest.raises(RuntimeError, match="MAX_PAGES"):
            _load_app(monkeypatch)

    def test_max_pages_non_integer_is_refused_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAX_PAGES", "lots")
        with pytest.raises(RuntimeError, match="MAX_PAGES"):
            _load_app(monkeypatch)
