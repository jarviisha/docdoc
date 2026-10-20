"""A PaddleX-protocol parser registry that never opens a socket (014).

Shared by the async OCR tests: the parse-only path, the worker, and the queued
`POST /v1/ocr`. `CountingPost` answers like an endpoint and records how often it
was asked, which is how "the OCR host was called once" is asserted.
"""

from __future__ import annotations

from typing import Any

from docdoc.ingest.parsers.paddlex import OcrEndpoint, PaddleXParser
from docdoc.ingest.registry import ParserRegistry


def response(text: str) -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {"type": "image", "width": 100.0, "height": 200.0},
            "ocrResults": [{"prunedResult": {"rec_texts": [text], "rec_boxes": [[0, 0, 10, 10]]}}],
        },
    }


class CountingPost:
    """A fake `post`: answers `text`, or raises `error`, and counts the calls."""

    def __init__(self, text: str = "INV-001", error: BaseException | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    def __call__(self, endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return response(self.text)


def paddle_registry(post: Any, *, languages: tuple[str, ...] = ("vie",)) -> ParserRegistry:
    """One configured parser, ``paddle``, whose probe answers healthy offline."""
    parser = PaddleXParser(
        OcrEndpoint(
            id="paddle",
            url="http://paddle.example/ocr",
            version="1.0.0",
            languages=languages,
            headers={},
        ),
        post=post,
    )
    parser.probe = lambda timeout_s=2.0: True  # type: ignore[method-assign]
    registry = ParserRegistry()
    registry.register(parser)
    return registry
