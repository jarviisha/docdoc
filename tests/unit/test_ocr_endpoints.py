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
        path = _write(
            tmp_path,
            [
                {"id": "paddle", "url": "http://host-a/ocr", "version": "1"},
                {
                    "id": "Not A Valid Id",
                    "url": "https://user:SEKRIT@host-b/ocr",
                    "version": "1",
                    "headers": {"Authorization": "Basic SEKRIT"},
                },
            ],
        )

        with pytest.raises(ValueError, match=r"endpoints\[1\]") as caught:
            load_endpoints(path)

        assert "SEKRIT" not in str(caught.value)


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
