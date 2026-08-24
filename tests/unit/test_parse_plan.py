"""The seam every parse-reuse claim rests on, and the proof it composes.

``ingest.parse()`` split into ``plan_parse`` and ``execute_plan`` so the pipeline
could learn a parse's identity *before* paying for it — ``document_id`` folds
``parser_id``, and routing is what chooses the parser, so the identity is not
knowable until routing has run and *is* knowable before the parser does.

That gap is where the cache lookup goes, and it is worth exactly one thing: that
the two halves still add up to the function they came from. T052 asked for this
and the first implementation pass marked it done without writing it, so the
central claim of the split went unasserted while the milestone reported complete.

**Every fixture, not one.** A split that composes on a digital PDF and diverges
on a scanned one, a rotated one, or one with no text layer would be a defect
whose symptom is a wrong cached document — and the fixture directory is the
cheapest place to find that out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from docdoc.ingest import parse
from docdoc.ingest.errors import IngestError
from docdoc.ingest.parse import execute_plan, plan_parse
from docdoc.kernel.identity import document_id_for

FIXTURES = sorted((Path("tests/fixtures/pdf")).glob("*.pdf"))


def test_there_are_fixtures_to_test() -> None:
    """Guard against the parametrised tests below passing vacuously."""
    assert len(FIXTURES) >= 5


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_plan_then_execute_equals_parse(fixture: Path) -> None:
    """The whole point of T052: the composition is the original.

    Both paths are attempted for every fixture, including the ones that cannot
    be parsed at all — ``encrypted.pdf`` and ``zero_pages.pdf`` are in this
    directory precisely because they fail, and the two halves must fail the *same
    way* as well as succeed the same way.
    """
    source = fixture.read_bytes()

    try:
        expected = parse(source)
    except IngestError as error:
        with pytest.raises(type(error)):
            execute_plan(plan_parse(source))
        return

    actual = execute_plan(plan_parse(source))

    assert actual.id == expected.id, "the composition produced a different identity"
    assert actual == expected, "the composition produced a different document"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_the_plan_predicts_the_identity_the_parse_produces(fixture: Path) -> None:
    """``plan.document_id`` is a promise about a parse that has not happened.

    If it were ever wrong the pipeline would look up one identity and store the
    result under another — a cache that misses forever, or worse, one that hits
    on the wrong entry.
    """
    source = fixture.read_bytes()

    try:
        plan = plan_parse(source)
    except IngestError:
        pytest.skip("this fixture is refused before a plan exists")

    predicted = plan.document_id
    document = execute_plan(plan)

    assert predicted == document.id


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_the_plan_carries_everything_document_id_needs(fixture: Path) -> None:
    """Asserted against ``document_id_for`` directly, input by input.

    ``plan.document_id`` could be right by accident — by reading the answer off
    something else — so this recomputes it from the four inputs ADR-0002 names
    and checks the plan carries each of them.
    """
    source = fixture.read_bytes()

    try:
        plan = plan_parse(source)
    except IngestError:
        pytest.skip("this fixture is refused before a plan exists")

    assert plan.file.blob_id.startswith("sha256:")
    assert plan.parser.id
    assert plan.parser.version
    assert plan.options_hash.startswith("sha256:")

    assert plan.document_id == document_id_for(
        blob_id=plan.file.blob_id,
        parser_id=plan.parser.id,
        parser_version=plan.parser.version,
        options_hash=plan.options_hash,
    )


def test_planning_needs_no_credentials_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-059 — computing an identity must never need a provider.

    The socket is patched to raise rather than merely left unused, because "it
    happens not to connect today" and "it cannot connect" are different
    guarantees and only the second one survives a refactor.
    """
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("planning a parse opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_DI_KEY", "AZURE_DI_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)

    plan = plan_parse(Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes())
    assert plan.document_id.startswith("sha256:")


def test_the_routing_verdict_is_decided_by_the_plan_not_the_parser() -> None:
    """FR-061 — what a cached parse still pays for.

    The text-layer assessment happens in the planning half, which is why a cache
    hit can skip the parser and still record the verdict. If it moved into
    ``execute_plan``, a reused document would arrive carrying a routing decision
    the run did not make, and Principle V's decision would stop being
    inspectable on a hit.
    """
    plan = plan_parse(Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes())

    assert plan.verdict is not None
    assert plan.verdict.rule_id, "the rule that routed this file"
    assert plan.verdict.pages, "the per-page evidence behind the verdict"

    document = execute_plan(plan)
    assert document.provenance.text_layer == plan.verdict, (
        "the document's recorded verdict must be the one the plan decided, not a "
        "second assessment made after the parse"
    )


def test_a_plan_can_be_executed_only_by_the_parser_it_selected() -> None:
    """The plan is a decision, and executing it must not re-decide.

    Two plans for two different files must not be interchangeable: if
    ``execute_plan`` re-routed or re-selected, the identity computed before the
    parse could stop describing the parse that happened.
    """
    first = plan_parse(Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes())
    second = plan_parse(Path("tests/fixtures/pdf/two_column.pdf").read_bytes())

    assert first.document_id != second.document_id
    assert execute_plan(first).id == first.document_id
    assert execute_plan(second).id == second.document_id
