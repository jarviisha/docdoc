"""The Definition of Done, asserted on the functions the service runs.

A PDF goes into the pipeline and any extracted value can be asked which page and
which rectangle it came from -- with no credentials, no network, no database, and
no service. That sentence is the project's founding statement of done, and this
file is the assertion of it. It drives `docdoc.ingest.parse` and
`docdoc.pipeline.run` directly, the same calls the worker makes for every run.

**The socket is patched to raise.** "No network" is otherwise a claim about what
the code is believed to do; here it is a property of the run. A run that reached
for a provider would fail loudly rather than quietly succeeding on a machine
that happened to have credentials in its environment.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.extraction.value import ExtractedValue
from docdoc.pipeline import run as run_pipeline

if TYPE_CHECKING:
    from docdoc.pipeline import PipelineResult

FIXTURE = "tests/fixtures/pdf/digital_invoice.pdf"
SCHEMA = "invoice@1"
ECHO = "tests/fixtures/echo"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # No credential anywhere. FR-029's "no credentials" is a property of the run
    # rather than of the developer's shell.
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_DI_KEY", "AZURE_DI_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a network call impossible rather than merely unnecessary."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the offline path opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _run(echo: str | Path = ECHO) -> PipelineResult:
    return run_pipeline(
        Path(FIXTURE).read_bytes(),
        schema=SCHEMA,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures(echo),
    )


def _values(node: Any) -> dict[str, ExtractedValue]:
    """Every extracted leaf, keyed by the path grounding addresses it by."""
    if isinstance(node, ExtractedValue):
        return {node.field_path: node}
    children = node.values() if isinstance(node, dict) else node
    found: dict[str, ExtractedValue] = {}
    for child in children:
        found.update(_values(child))
    return found


def test_parse_runs_with_no_credentials_and_no_network(no_network: None) -> None:
    from docdoc.ingest import parse

    document = parse(Path(FIXTURE).read_bytes())
    assert document.id.startswith("sha256:")
    assert len(document.pages) >= 1
    # Principle V's routing decision, recorded on every run rather than only when
    # it is obeyed.
    assert document.provenance.text_layer is not None
    assert document.provenance.text_layer.rule_id


def test_every_value_carries_a_page_and_a_rectangle(no_network: None) -> None:
    """SC-001, and the half of the Definition of Done that says *where*.

    **Both halves of the criterion, which is what it asks for as amended.** A
    located value carries a page and a rectangle; an unlocated one carries
    neither. Stated together because each alone is trivially satisfiable in the
    wrong direction: count only grounded values and locating nothing passes, and
    demand a page for every extracted value and only fabricating one passes.
    """
    result = _run()
    assert result.grounding is not None
    outcomes = list(result.grounding.outcomes.values())
    assert outcomes, "the schema declares fields; none were grounded or refused"

    grounded = [o for o in outcomes if o.status.value in {"exact", "fuzzy"}]
    ungrounded = [o for o in outcomes if o.status.value == "ungrounded"]
    assert grounded, "nothing grounded, so the first half asserts nothing"
    assert ungrounded, (
        "nothing ungrounded on this fixture, so the second half asserts nothing. If the "
        "fixture or the grounder changed so that everything now locates, this test needs a "
        "case that does not — the guarantee is about the absence, not only the presence"
    )

    for outcome in grounded:
        assert outcome.pages, f"{outcome.field_path} is grounded with no page"
        assert outcome.geometry, f"{outcome.field_path} is grounded with no rectangle"

    for outcome in ungrounded:
        assert not outcome.pages, f"{outcome.field_path} is ungrounded and still carries a page"
        assert not outcome.geometry, (
            f"{outcome.field_path} is ungrounded and still carries a rectangle"
        )


def test_ungrounded_values_stay_machine_distinguishable(no_network: None) -> None:
    """Principle II: never emit an ungrounded value as if it were grounded."""
    result = _run()
    assert result.grounding is not None
    for outcome in result.grounding.outcomes.values():
        assert outcome.status.value in {"exact", "fuzzy", "ungrounded", "not_applicable"}
        if outcome.status.value == "ungrounded":
            assert outcome.geometry is None or outcome.geometry == ()


def test_an_ungrounded_value_is_still_a_value(no_network: None) -> None:
    """The other thing SC-001's amendment has to keep true.

    "Carries neither a page nor a rectangle" must not quietly become "was
    dropped". An ungrounded value is a real extraction the grounder could not
    place, and the whole point of reporting it as such — rather than omitting it —
    is that a reader can see what was extracted and decide for themselves.
    """
    result = _run()
    assert result.grounding is not None
    assert result.extraction is not None
    values = _values(result.extraction.values)
    ungrounded = [
        path for path, o in result.grounding.outcomes.items() if o.status.value == "ungrounded"
    ]
    assert ungrounded
    for path in ungrounded:
        assert path in values, f"{path} is ungrounded and has no extracted value"
        assert values[path].value not in (None, ""), f"{path} was reported without its value"


def test_an_invalid_document_still_reports_everything(no_network: None, tmp_path: Path) -> None:
    """A failed validation is a successful run whose answer is "no".

    Built by violating a declared constraint — ``currency`` carries an ``enum``
    — rather than a cross-field rule, because ``invoice@1`` declares no rules.
    """
    fixtures = json.loads(Path(ECHO, "invoice@1.json").read_text())
    fixtures["currency"]["value"] = "XYZ"
    (tmp_path / "invoice@1.json").write_text(json.dumps(fixtures))

    result = _run(tmp_path)
    assert result.failed_stage is None, "an invalid document is not a failed run"
    assert result.validation is not None
    assert result.validation.verdict.value != "valid"
    assert result.grounding is not None
    assert result.grounding.outcomes, "an invalid document still reports every field"
