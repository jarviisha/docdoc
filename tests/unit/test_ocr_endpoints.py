"""`OcrEndpoint` and `load_endpoints` — the configuration half of ADR-0021.

A configured endpoint is reached under the deployment's own authority, so its
URL and headers are exactly the two fields that must never survive into a
`repr`, a log, or an error message (the gateway design's "Credential
leakage" anti-goal; ADR-0021 §3). These tests pin that `load_endpoints`
refuses a bad configuration file at startup -- the same "fail loud, fail
early" the key-ring and limits loaders already follow -- without ever
quoting the value that made an entry bad.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

import pytest

from docdoc.ingest.parsers.paddlex import OcrEndpoint, load_endpoints


def _write(tmp_path: Path, endpoints: list[dict[str, object]]) -> Path:
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps({"endpoints": endpoints}))
    return path


class TestLoadEndpoints:
    def test_loads_endpoints_with_defaults(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            [{"id": "paddle", "url": "http://127.0.0.1:18080/ocr", "version": "1.0.0"}],
        )

        (endpoint,) = load_endpoints(path)

        assert endpoint.id == "paddle"
        assert endpoint.media_types == frozenset({"application/pdf", "image/jpeg", "image/png"})
        assert endpoint.languages == ()
        assert endpoint.max_pages is None
        assert endpoint.headers == {}

    def test_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            [
                {"id": "paddle", "url": "http://host-a/ocr", "version": "1"},
                {"id": "paddle", "url": "http://host-b/ocr", "version": "1"},
            ],
        )

        with pytest.raises(ValueError, match="paddle"):
            load_endpoints(path)

    def test_rejects_builtin_id(self, tmp_path: Path) -> None:
        path = _write(tmp_path, [{"id": "gcv", "url": "http://host/ocr", "version": "1"}])

        with pytest.raises(ValueError, match="gcv"):
            load_endpoints(path)

    def test_rejects_non_http_url(self, tmp_path: Path) -> None:
        path = _write(tmp_path, [{"id": "paddle", "url": "ftp://host/ocr", "version": "1"}])

        with pytest.raises(ValueError, match=r"endpoints\[0\]"):
            load_endpoints(path)

    def test_error_names_index_not_values(self, tmp_path: Path) -> None:
        # The bad entry's url itself fails validation (wrong scheme), which is
        # exactly the case where pydantic's own `ValidationError` embeds the
        # offending value (`errors()[i]["input"]`) -- so this is the scenario
        # that actually exercises whether SEKRIT can leak, not just one where
        # a *coincidentally* unrelated field (id) is the one that is invalid
        # while a well-formed secret rides along unexamined.
        path = _write(
            tmp_path,
            [
                {"id": "paddle", "url": "http://host-a/ocr", "version": "1"},
                {
                    "id": "paddle-2",
                    "url": "ftp://user:SEKRIT@host-b/ocr",
                    "version": "1",
                    "headers": {"Authorization": "Basic SEKRIT"},
                },
            ],
        )

        with pytest.raises(ValueError, match=r"endpoints\[1\]") as caught:
            load_endpoints(path)

        assert "SEKRIT" not in str(caught.value)
        # Not just this function's own message: `from None` (rather than
        # `from error`) is what keeps pydantic's underlying `ValidationError`
        # -- which *does* embed the bad url verbatim -- off `__cause__`, so it
        # never appears when something downstream (a log handler, an
        # unhandled-exception report) formats the full chain.
        rendered = "".join(traceback.format_exception(caught.value))
        assert "SEKRIT" not in rendered
        assert caught.value.__cause__ is None


class TestHeadersRejectControlCharacters:
    """Finding 2 of the 012 review: a header value carrying CR/LF makes
    `http.client.putheader` raise a bare `ValueError` that quotes the
    credential in full (`ValueError("Invalid header value b'Bearer
    s3cret\\n'")`). `OcrEndpoint` must refuse that at load time, before the
    endpoint is ever used, and never echo the value while doing so.
    """

    def test_the_validator_itself_rejects_a_control_character(self) -> None:
        # The constructor's raw `pydantic.ValidationError` is *not* the
        # trusted boundary -- its own default `str()` embeds the whole
        # offending field verbatim (`input=...`), same as `_url_is_http`'s
        # value does for a bad url. That is exactly why `load_endpoints`
        # below, not this constructor, is what a deployment's configuration
        # actually goes through; this only pins that the validator fires.
        with pytest.raises(ValueError, match="Authorization"):
            OcrEndpoint(
                id="paddle",
                url="http://host/ocr",
                version="1",
                headers={"Authorization": "Bearer SEKRIT\n"},
            )

    def test_load_endpoints_refuses_it_too_naming_entry_and_header_not_value(
        self, tmp_path: Path
    ) -> None:
        path = _write(
            tmp_path,
            [
                {
                    "id": "paddle",
                    "url": "http://host/ocr",
                    "version": "1",
                    "headers": {"X-Key": "a\r\nb"},
                }
            ],
        )

        with pytest.raises(ValueError, match=r"endpoints\[0\]") as caught:
            load_endpoints(path)

        assert "X-Key" in str(caught.value)
        assert "a\r\nb" not in str(caught.value)
        rendered = "".join(traceback.format_exception(caught.value))
        assert "a\r\nb" not in rendered
        assert caught.value.__cause__ is None


class TestOcrEndpointRepr:
    def test_repr_hides_url_and_headers(self) -> None:
        endpoint = OcrEndpoint(
            id="paddle",
            url="https://user:SEKRIT@ocr.internal/ocr",
            version="1.0.0",
            headers={"Authorization": "Basic SEKRIT"},
        )

        rendered = repr(endpoint)

        assert "SEKRIT" not in rendered
        assert "ocr.internal" not in rendered
        assert "paddle" in rendered  # the id is exactly what a repr may name
