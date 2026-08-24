"""The Definition of Done, run the way a person would run it.

A PDF goes in one end of a command and a human can ask any extracted value which
page and which rectangle it came from — with no credentials, no network, no
database, and no service. That sentence is the project's founding statement of
done, and this file is the assertion of it.

**The socket is patched to raise.** "No network" is otherwise a claim about what
the code is believed to do; here it is a property of the run. A command that
reached for a provider would fail loudly rather than quietly succeeding on a
machine that happened to have credentials in its environment.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from docdoc.cli import EXIT_OK, main

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURE = "tests/fixtures/pdf/digital_invoice.pdf"
SCHEMA = "invoice@1"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", str(Path("schemas").resolve()))
    monkeypatch.setenv("DOCDOC_MODEL_ADAPTERS", "echo")
    monkeypatch.setenv("DOCDOC_ECHO_FIXTURES", str(Path("tests/fixtures/echo").resolve()))
    monkeypatch.delenv("DOCDOC_STORE_ROOT", raising=False)
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


def run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_parse_runs_with_no_credentials_and_no_network(
    no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(["parse", FIXTURE, "--json"], capsys)
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["document_id"].startswith("sha256:")
    assert payload["pages"] >= 1
    # Principle V's routing decision, recorded on every run rather than only when
    # it is obeyed.
    assert payload["text_layer"]["rule_id"]


def test_every_value_carries_a_page_and_a_rectangle(
    no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """SC-001, and the half of the Definition of Done that says *where*.

    Asserted over grounded values only, and that is the honest scope: a value the
    grounder could not locate has no page to carry, and SC-001's "where geometry
    exists" is exactly that qualification. Claiming a rectangle for an ungrounded
    value is the failure Principle II exists to prevent.
    """
    code, out = run(["inspect", FIXTURE, "--schema", SCHEMA, "--json"], capsys)
    assert code == EXIT_OK

    fields = json.loads(out)["fields"]
    assert fields, "the schema declares fields; none reached the output"

    grounded = [row for row in fields if row["grounding"] in {"exact", "fuzzy"}]
    assert grounded, "nothing grounded, so this asserts nothing"
    for row in grounded:
        assert row["page"] is not None, f"{row['field']} is grounded with no page"
        assert row["bbox"] is not None, f"{row['field']} is grounded with no rectangle"
        assert len(row["bbox"]) == 4


def test_ungrounded_values_stay_machine_distinguishable(
    no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Principle II: never emit an ungrounded value as if it were grounded."""
    _, out = run(["inspect", FIXTURE, "--schema", SCHEMA, "--json"], capsys)
    for row in json.loads(out)["fields"]:
        assert row["grounding"] in {"exact", "fuzzy", "ungrounded", "not_applicable"}
        if row["grounding"] == "ungrounded":
            assert row["bbox"] is None


def test_extract_and_inspect_are_one_run_with_two_renderings(
    no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """SC-014's discipline at the command level: one sequence, not two."""
    _, extracted = run(["extract", FIXTURE, "--schema", SCHEMA, "--json"], capsys)
    _, inspected = run(["inspect", FIXTURE, "--schema", SCHEMA, "--json"], capsys)

    first, second = json.loads(extracted), json.loads(inspected)
    assert first["processing_id"] == second["processing_id"]
    assert first["fields"] == second["fields"]


def test_eval_reproduces_the_committed_numbers(
    no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """T106 — the CLI is a front end, not a second implementation.

    The reference is ``examples/evaluate_golden_set.py``, which printed these
    numbers before this command existed. Comparing against the evaluation layer
    directly rather than against a hard-coded table, so that a deliberate change
    to a metric updates one place and this test follows it.
    """
    from docdoc.evaluation import evaluate, load_golden_set, load_prediction_set, schema_facts
    from docdoc.extraction import SchemaRegistry

    registry = SchemaRegistry.from_paths([Path("schemas")])
    facts = schema_facts([registry.describe(name) for name in registry.identities()])
    expected = evaluate(
        load_golden_set(Path("datasets/mvp/manifest.json"), facts=facts),
        load_prediction_set(Path("datasets/mvp/predictions"), facts=facts),
        facts=facts,
    )

    code, out = run(
        [
            "eval",
            "datasets/mvp/manifest.json",
            "--predictions",
            "datasets/mvp/predictions",
            "--json",
        ],
        capsys,
    )
    assert code == EXIT_OK

    payload = json.loads(out)
    assert payload["report_id"] == expected.report_id
    for name in ("field_accuracy", "coverage", "missing_rate", "incorrect_rate", "grounding_rate"):
        assert payload["metrics"]["micro"][name]["value"] == expected.metrics.micro[name].value


def test_an_invalid_document_exits_one_and_still_reports_everything(
    no_network: None, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A failed validation is a successful run whose answer is "no".

    Built by violating a declared constraint — ``currency`` carries an ``enum``
    — rather than a cross-field rule, because ``invoice@1`` declares no rules.
    That is worth stating: the obvious way to write this test is to break the
    ``sum(line_items) == total`` relation Principle VII names as the example, and
    on this schema that would break nothing and the test would pass while
    asserting nothing at all.
    """
    from docdoc.cli import EXIT_INVALID
    from docdoc.extraction import SchemaRegistry
    from docdoc.extraction.adapters.echo import EchoAdapter
    from docdoc.pipeline import run as run_pipeline

    fixtures = json.loads(Path("tests/fixtures/echo/invoice@1.json").read_text())
    fixtures["currency"]["value"] = "XYZ"
    broken = tmp_path / "invoice@1.json"
    broken.write_text(json.dumps(fixtures))

    result = run_pipeline(
        Path(FIXTURE).read_bytes(),
        schema=SCHEMA,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures(tmp_path),
    )
    assert result.validation is not None
    assert result.validation.verdict.value != "valid", (
        "the fixture was edited to break the sum rule; if this passes, the rule "
        "did not run and the exit-code assertion below would prove nothing"
    )

    code, out = run(
        [
            "extract",
            FIXTURE,
            "--schema",
            SCHEMA,
            "--json",
            "--echo-fixtures",
            str(tmp_path),
        ],
        capsys,
    )
    assert code == EXIT_INVALID
    payload = json.loads(out)
    assert payload["verdict"] != "valid"
    assert payload["fields"], "an invalid document still reports every field"
