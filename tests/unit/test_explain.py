"""Derivation records — what ADR-0003 accepted unreadable cache keys for.

ADR-0003 conceded that cache keys "cannot be computed by hand or eyeballed in
logs" on one explicit condition: that something would explain them. Every
stored artifact carries a derivation record, read through
``docdoc.artifacts.derivation`` with the folded-input names from
``docdoc.pipeline.stages.folded_inputs_for``. Without that,
the first cache-correctness incident is unarguable in both directions — nobody can
show the reuse was right, and nobody can show it was wrong.

The test that matters most is the leak one. A derivation is the single most
likely thing to be pasted into a ticket, so it explains *identities* and never
documents.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.artifacts import FileArtifactStore
from docdoc.artifacts.derivation import derivation_chain, derivation_of
from docdoc.artifacts.errors import ArtifactError
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import run
from docdoc.pipeline.stages import folded_inputs_for

# SC-013: the offline suite must pass on a base install, which has no native PDF
# reader. Every test in this module reaches a real parse through the `stored`
# fixture, so the whole module skips rather than each test guarding itself.
pytest.importorskip("pypdfium2")

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


def _explained_chain(store: FileArtifactStore, processing_id: str) -> list[dict[str, Any]]:
    """Every link, with the folded-input names the store itself cannot know."""
    links = []
    for record in derivation_chain(store, processing_id):
        link = record.model_dump(mode="json")
        link["folded_inputs"] = list(record.folded_inputs or folded_inputs_for(record.stage))
        links.append(link)
    return links


def test_an_explanation_carries_no_document_content(
    stored: tuple[FileArtifactStore, str],
) -> None:
    """FR-025 and SC-007 — it explains identities, not documents.

    Asserted over the whole serialised chain rather than field by field, because
    the failure being guarded against is a field somebody adds later without
    thinking about where it ends up.
    """
    store, processing_id = stored
    chain = _explained_chain(store, processing_id)
    rendered = json.dumps(chain)

    assert DISTINCTIVE not in rendered, "the document's text reached a derivation"
    assert "Acme" not in rendered, "an extracted value reached a derivation"
    assert "1240.00" not in rendered, "an extracted value reached a derivation"

    # And the folded *names* are there, which is what makes it useful at all.
    assert chain[0]["folded_inputs"], "a derivation with no folded inputs"
    assert "validation_options" in chain[0]["folded_inputs"]


def test_folded_input_names_are_names_and_never_values(
    stored: tuple[FileArtifactStore, str],
) -> None:
    """ "prompt_hash" is a name; the prompt is a document."""
    store, processing_id = stored
    chain = _explained_chain(store, processing_id)

    extract_link = next(link for link in chain if link["stage"] == "extract")
    assert "prompt_hash" in extract_link["folded_inputs"]
    assert "model_id" in extract_link["folded_inputs"]
    for name in extract_link["folded_inputs"]:
        assert not name.startswith("sha256:"), f"{name} is a value, not a name"


def test_an_identity_with_no_record_says_so_and_does_not_guess(tmp_path: Path) -> None:
    """FR-023 — a derivation is read from the record a write left behind.

    A reconstruction would be a guess wearing the costume of a record.
    """
    store = FileArtifactStore(tmp_path)
    absent = "sha256:" + "0" * 64
    assert derivation_of(store, absent) is None
    assert list(derivation_chain(store, absent)) == []


def test_a_malformed_identity_does_not_escape_the_store_root(tmp_path: Path) -> None:
    """An identity is a filename here, so a path separator would be a traversal."""
    with pytest.raises(ArtifactError):
        derivation_of(FileArtifactStore(tmp_path), "../../etc/passwd")
