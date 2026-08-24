"""T053 — no temporary file survives a parse, successful or not (FR-030, SC-014).

The constitution makes temporary file cleanup an MVP security requirement, and
for good reason: a leaked temp file is a copy of someone's document sitting on
disk after the process that was trusted with it has finished.

The current adapters read from memory and write nothing, so these tests are as
much a guard against a future change as a check on today's code -- the moment an
adapter starts spilling to disk, this fails.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

import pytest

from docdoc.ingest import parse
from docdoc.ingest.errors import IngestError

# SC-013: the offline suite must pass on a base install — one with no provider
# SDK at all. Constitution XII: "Provider adapters MUST have integration tests;
# those tests MUST NOT be required to run the unit and property suites." These
# tests exercise a real parse's temporary files, so they skip rather than fail when it is absent.
pytest.importorskip("pymupdf")

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def temp_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every temp-file mechanism at a directory we can inspect."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def survivors(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


class TestSuccessfulParses:
    def test_nothing_is_left_behind(self, temp_dir: Path) -> None:
        parse((FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes())

        assert survivors(temp_dir) == []

    def test_nothing_is_left_behind_across_the_whole_fixture_set(self, temp_dir: Path) -> None:
        for path in sorted((FIXTURES / "pdf").glob("*.pdf")):
            # Refusals are covered below; here only the residue matters.
            with contextlib.suppress(IngestError):
                parse(path.read_bytes())

        assert survivors(temp_dir) == []


class TestFailedParses:
    @pytest.mark.parametrize(
        "fixture",
        ["pdf/encrypted.pdf", "pdf/scanned_contract.pdf"],
        ids=["encrypted", "no-recognition-parser-available"],
    )
    def test_a_refusal_leaves_nothing_behind(self, temp_dir: Path, fixture: str) -> None:
        with pytest.raises(IngestError):
            parse((FIXTURES / fixture).read_bytes())

        assert survivors(temp_dir) == []

    def test_a_truncated_file_leaves_nothing_behind(self, temp_dir: Path) -> None:
        data = (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()[:200]

        with pytest.raises(IngestError):
            parse(data)

        assert survivors(temp_dir) == []

    def test_an_interruption_mid_parse_leaves_nothing_behind(self, temp_dir: Path) -> None:
        """The case cleanup usually misses: not a handled failure, but a
        KeyboardInterrupt or a killed worker partway through."""
        from docdoc.ingest.parsers import pdf_text

        real_open = pdf_text._open

        def interrupt(source: object) -> object:
            document = real_open(source)  # type: ignore[arg-type]
            document.close()
            raise KeyboardInterrupt

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(pdf_text, "_open", interrupt)
            with pytest.raises(KeyboardInterrupt):
                parse((FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes())

        assert survivors(temp_dir) == []
