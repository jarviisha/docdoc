"""``docdoc explain`` — the tool ADR-0003 accepted unreadable cache keys for.

ADR-0003 conceded that cache keys "cannot be computed by hand or eyeballed in
logs" on one explicit condition: that something would explain them. Without that,
the first cache-correctness incident is unarguable in both directions — nobody can
show the reuse was right, and nobody can show it was wrong.

The test that matters most is the leak one. An explanation is the single most
likely thing to be pasted into a ticket, so it explains *identities* and never
documents.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from docdoc.artifacts import FileArtifactStore
from docdoc.artifacts.derivation import derivation_chain, derivation_of
from docdoc.cli import main
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import run

# SC-013: the offline suite must pass on a base install, which has no native PDF
# reader. Every test in this module reaches a real parse through the `stored`
# fixture, so the whole module skips rather than each test guarding itself.
pytest.importorskip("pymupdf")

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: A string that appears in the fixture's text and in nothing else, so finding it
#: anywhere in an explanation is unambiguous evidence of a leak.
DISTINCTIVE = "INV-001"


@pytest.fixture
def stored(tmp_path: Path) -> tuple[FileArtifactStore, str]:
    """One completed run, and the identity it produced."""
    store = FileArtifactStore(tmp_path)
    result = run(
        FIXTURE.read_bytes(),
        schema=SCHEMA,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        store=store,
    )
    assert result.processing_id is not None
    return store, result.processing_id


def test_a_derivation_names_the_stage_the_processor_and_the_input(
    stored: tuple[FileArtifactStore, str],
) -> None:
    """FR-023 — what would have to change to move this identity."""
    store, processing_id = stored
    record = derivation_of(store, processing_id)

    assert record is not None
    assert record.stage == "validate"
    assert record.processor_id
    assert record.processor_version
    assert record.options_hash.startswith("sha256:")
    assert record.input_artifact_id is not None, "the grounding artifact it came from"


def test_the_chain_reaches_the_source_blob_in_four_hops(
    stored: tuple[FileArtifactStore, str],
) -> None:
    """FR-024 — validate, ground, extract, parse, and then the blob.

    The parse artifact's input *is* the blob id, which is what makes the walk
    terminate at the document rather than at the first parse. That is also the
    one thing FR-022 owes a future garbage collector.
    """
    store, processing_id = stored
    chain = derivation_chain(store, processing_id)

    assert [link.stage for link in chain] == ["validate", "ground", "extract", "parse"]
    assert chain[-1].input_artifact_id is not None
    assert chain[-1].input_artifact_id.startswith("sha256:")

    # Each link's input is the next link's identity: the chain composes.
    for earlier, later in itertools.pairwise(chain):
        assert earlier.input_artifact_id == later.artifact_id


def test_an_explanation_carries_no_document_content(
    stored: tuple[FileArtifactStore, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-025 and SC-007 — it explains identities, not documents.

    Asserted over the whole rendered output rather than field by field, because
    the failure being guarded against is a field somebody adds later without
    thinking about where it ends up.
    """
    _, processing_id = stored

    code = main(["explain", processing_id, "--chain", "--store", str(tmp_path), "--json"])
    assert code == 0

    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert DISTINCTIVE not in rendered, "the document's text reached an explanation"
    assert "Acme" not in rendered, "an extracted value reached an explanation"
    assert "1240.00" not in rendered, "an extracted value reached an explanation"

    # And the folded *names* are there, which is what makes it useful at all.
    assert payload["derivation"]["folded_inputs"], "an explanation with no folded inputs"
    assert "validation_options" in payload["derivation"]["folded_inputs"]


def test_folded_input_names_are_names_and_never_values(
    stored: tuple[FileArtifactStore, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "prompt_hash" is a name; the prompt is a document."""
    _, processing_id = stored
    main(["explain", processing_id, "--chain", "--store", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    extract_link = next(link for link in payload["chain"] if link["stage"] == "extract")
    assert "prompt_hash" in extract_link["folded_inputs"]
    assert "model_id" in extract_link["folded_inputs"]
    for name in extract_link["folded_inputs"]:
        assert not name.startswith("sha256:"), f"{name} is a value, not a name"


def test_an_identity_with_no_record_says_so_and_does_not_guess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-023 — a derivation is read from the record a write left behind.

    A reconstruction would be a guess wearing the costume of a record, and the
    whole value of this command is that it reports what happened.
    """
    absent = "sha256:" + "0" * 64
    code = main(["explain", absent, "--store", str(tmp_path), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["derivation"] is None
    assert payload["reason"] == "not_in_store"


def test_a_run_with_no_store_has_no_derivation_to_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-017 and FR-023 together: no store, no record, and it says which."""
    code = main(["explain", "sha256:" + "0" * 64, "--no-store", "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["derivation"] is None
    assert payload["reason"] == "no_store"


def test_a_malformed_identity_does_not_escape_the_store_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An identity is a filename here, so a path separator would be a traversal."""
    code = main(["explain", "../../etc/passwd", "--store", str(tmp_path), "--json"])
    assert code in {0, 2}, "it must refuse, not read"
    assert "root:" not in capsys.readouterr().out
