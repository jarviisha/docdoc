"""T061 — what moves the artifact id, and what must not (EXT-21…EXT-23, SC-009).

ADR-0003's rule is one sentence: fold every input that can change the result, and
nothing that cannot. Both halves need testing, because getting the first wrong is a
stale-cache correctness bug and getting the second wrong throws away the partial
reuse the chain exists to provide.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import Effort, ExtractionOptions, SchemaRegistry, Thinking, extract
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.extraction.identity import (
    EXTRACTOR_ID,
    extraction_artifact_id_for,
    options_hash_for_extraction,
)
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\nTotal 1,240.00\n"

_BASE = {
    "schema_identity": "invoice@1",
    "schema_hash": "sha256:" + "a" * 64,
    "prompt_hash": "sha256:" + "b" * 64,
    "projection_id": "response-shape@1",
    "model_id": "a-model",
    "model_version": "1",
    "max_tokens": 8192,
    "effort": "high",
    "thinking": "adaptive",
    "input_budget_tokens": 200_000,
}


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def echo() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


# -- the folded set ----------------------------------------------------------


def test_the_same_inputs_hash_identically() -> None:
    assert options_hash_for_extraction(**_BASE) == options_hash_for_extraction(**_BASE)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("schema_identity", "invoice@2"),
        ("schema_hash", "sha256:" + "c" * 64),
        ("prompt_hash", "sha256:" + "d" * 64),
        ("projection_id", "response-shape@2"),
        ("model_id", "another-model"),
        ("model_version", "2"),
        ("max_tokens", 4096),
        ("effort", "low"),
        ("thinking", "disabled"),
        ("input_budget_tokens", 100_000),
    ],
)
def test_every_folded_input_moves_the_options_hash(field: str, changed: Any) -> None:
    """EXT-21, SC-009 -- an input that cannot invalidate is an input that can go stale."""
    assert options_hash_for_extraction(**{**_BASE, field: changed}) != options_hash_for_extraction(
        **_BASE
    )


def test_the_folded_set_is_exactly_what_adr_0003_and_r4_name() -> None:
    """Pins the set, so adding or dropping one is a deliberate change.

    Notably absent: ``temperature``, ``top_p``, and ``seed``. The chosen provider's
    current models reject the first two outright and have never had the third, so
    folding them would be dead code -- research.md R4 refines ADR-0003's Extract row
    on exactly this point.
    """
    import inspect

    parameters = set(inspect.signature(options_hash_for_extraction).parameters)
    assert parameters == set(_BASE)
    assert not parameters & {"temperature", "top_p", "top_k", "seed"}


# -- the artifact chain ------------------------------------------------------


def test_the_artifact_id_derives_from_the_document_id() -> None:
    """EXT-23 -- the chain composes, which is what makes partial reuse work."""
    first = extraction_artifact_id_for(
        document_id="sha256:doc-a",
        extractor_id=EXTRACTOR_ID,
        extractor_version="1.0.0",
        options_hash="sha256:opts",
    )
    second = extraction_artifact_id_for(
        document_id="sha256:doc-b",
        extractor_id=EXTRACTOR_ID,
        extractor_version="1.0.0",
        options_hash="sha256:opts",
    )
    assert first != second


def test_the_extractor_version_moves_the_artifact_id() -> None:
    """FR-036 -- a processor that changes output must change its version."""
    args = {
        "document_id": "sha256:doc",
        "extractor_id": EXTRACTOR_ID,
        "options_hash": "sha256:opts",
    }
    assert extraction_artifact_id_for(**args, extractor_version="1.0.0") != (
        extraction_artifact_id_for(**args, extractor_version="1.0.1")
    )


def test_the_adapter_version_reaches_the_artifact_id(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The gap the first implementation had.

    ``adapter_version`` was recorded in provenance but folded nowhere, so an
    adapter fix that changed results would have returned a stale artifact. It is
    now embedded in ``extractor_version``, the way ingest embeds a library version
    in ``parser_version`` -- recording a change makes it visible, only folding it
    makes it invalidating.
    """
    import json
    import pathlib

    payload = json.loads(pathlib.Path("tests/fixtures/echo/invoice@1.json").read_text())
    document = make_document(DOCUMENT_TEXT)
    original = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    upgraded = extract(
        document,
        schema="invoice@1",
        registry=registry,
        adapter=EchoAdapter({"invoice@1": payload}, version="1.0.1"),
    )
    assert upgraded.provenance.extractor_version != original.provenance.extractor_version
    assert upgraded.artifact_id != original.artifact_id


def test_the_extractor_version_embeds_the_adapter(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    result = extract(
        make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=echo
    )
    assert result.provenance.extractor_version == "1.0.0+echo-1.0.0"


# -- what must NOT move it ---------------------------------------------------


def test_transport_settings_are_not_in_the_folded_set() -> None:
    """EXT-22, FR-027 -- true by construction, because they live in another type.

    ``TransportSettings`` is a separate type precisely so that this cannot be got
    wrong by forgetting. The assertion is that the signature has no room for them.
    """
    import inspect

    parameters = set(inspect.signature(options_hash_for_extraction).parameters)
    assert not parameters & {
        "max_attempts",
        "attempt_timeout_s",
        "deadline_s",
        "initial_backoff_s",
        "jitter",
        "transport",
    }


def test_repeated_extraction_with_identical_inputs_is_identical(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    document = make_document(DOCUMENT_TEXT)
    first = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    second = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    assert first.artifact_id == second.artifact_id


def test_only_the_schema_changing_reuses_the_parse(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """SC-010 -- the cost saving the artifact chain exists for.

    Changing the schema must not invalidate the document, because re-parsing means
    re-running an ingest provider, which is the expensive call in the chain.
    """
    document = make_document(DOCUMENT_TEXT)
    v1 = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    v2 = extract(document, schema="invoice@2", registry=registry, adapter=echo)
    assert v1.provenance.document_id == v2.provenance.document_id == document.id
    assert v1.artifact_id != v2.artifact_id


@pytest.mark.parametrize(
    ("field", "value"),
    [("effort", Effort.LOW), ("thinking", Thinking.DISABLED), ("max_tokens", 4096)],
)
def test_result_affecting_options_move_the_artifact_id(
    registry: SchemaRegistry, echo: EchoAdapter, field: str, value: Any
) -> None:
    document = make_document(DOCUMENT_TEXT)
    base = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    changed = extract(
        document,
        schema="invoice@1",
        registry=registry,
        adapter=echo,
        options=ExtractionOptions(**{field: value}),
    )
    assert changed.artifact_id != base.artifact_id


def test_a_different_document_gives_a_different_artifact(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The source bytes must differ, not just the text.

    ``document_id`` derives from the blob, the parser, its version, and the
    options -- never from the text (ADR-0002). Two ``make_document`` calls that
    differ only in text share a blob and therefore share an id, which is correct
    for the kernel and a trap for a test: passing different text alone would make
    this assertion pass for the wrong reason once it started passing at all.
    """
    first = extract(
        make_document("one document", data=b"%PDF-1.7 first"),
        schema="invoice@1",
        registry=registry,
        adapter=echo,
    )
    second = extract(
        make_document("another document entirely", data=b"%PDF-1.7 second"),
        schema="invoice@1",
        registry=registry,
        adapter=echo,
    )
    assert first.provenance.document_id != second.provenance.document_id
    assert first.artifact_id != second.artifact_id
