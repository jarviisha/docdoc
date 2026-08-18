"""T063 — the grounding boundary, in a file named after the thing it guards.

Covers SC-018, EXT-24, **FR-032**, and the first clause of **FR-047**. Those two FR
ids are written here because a convergence pass found them cited by no test and no
source file anywhere in the repository. The behaviour was covered all along -- this
file is the coverage -- but a requirement traceable to nothing reads exactly like a
requirement nobody implemented, and the only way to tell the two apart was to
re-derive it by hand. FR-047's stronger clause, that no grounding input may enter
the extract stage's options hash, is held elsewhere: see
``test_the_folded_set_is_exactly_what_adr_0003_names``.

Every grounding field on every value of every result is unresolved, because
resolving them is Milestone 4's stage with its own artifact under ADR-0003.

This lives in its own file on purpose. The assertions began life inside
``test_provenance_recording.py`` and the second analysis pass found the task that
claimed to have written them naming a file that did not exist. Burying a stage
boundary inside a test about something else is how it disappears in a refactor:
nobody deleting provenance assertions expects to be deleting the check that keeps
two milestones apart.

The structural test at the bottom is the one that matters most. The value
assertions would still pass if a code path existed that set a grounding status but
happened not to run for these fixtures.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import SchemaRegistry, extract
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.extraction.value import ExtractedValue
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\nTotal 1,240.00\n"


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def echo() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def _result(registry: SchemaRegistry, echo: EchoAdapter, identity: str = "invoice@1") -> Any:
    return extract(make_document(DOCUMENT_TEXT), schema=identity, registry=registry, adapter=echo)


def _values(tree: Any):
    """Every ExtractedValue in a result, at every depth."""
    for node in tree.values():
        if isinstance(node, ExtractedValue):
            yield node
        elif isinstance(node, tuple):
            for entry in node:
                yield from _values(entry)
        else:
            yield from _values(node)


@pytest.mark.parametrize("identity", ["invoice@1", "invoice@2", "receipt@1"])
def test_every_grounding_field_is_unresolved(
    registry: SchemaRegistry, echo: EchoAdapter, identity: str
) -> None:
    """EXT-24 -- across every schema, at every depth, including repeating groups."""
    values = list(_values(_result(registry, echo, identity).values))
    assert values, "a test that found no values would pass for the wrong reason"
    for value in values:
        assert value.grounding is None, f"{value.field_path} was grounded one milestone early"
        assert value.grounding_score is None
        assert value.grounded is False


@pytest.mark.parametrize("identity", ["invoice@1", "invoice@2", "receipt@1"])
def test_the_calibration_fields_are_reserved_and_empty(
    registry: SchemaRegistry, echo: EchoAdapter, identity: str
) -> None:
    """ADR-0004 -- a blended score may only ever come from a versioned calibrator."""
    for value in _values(_result(registry, echo, identity).values):
        assert value.calibrated_confidence is None
        assert value.calibrator_version is None


def test_no_module_in_the_layer_writes_a_grounding_status() -> None:
    """The structural half of the assertion above.

    The value tests would still pass if a code path existed that set a status but
    happened not to run for these fixtures. This scans for the assignment itself.
    """
    import ast
    import pathlib

    import docdoc.extraction

    root = pathlib.Path(docdoc.extraction.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            is_grounding_kwarg = isinstance(node, ast.keyword) and node.arg in (
                "grounding",
                "grounding_score",
            )
            if is_grounding_kwarg and not (
                isinstance(node.value, ast.Constant) and node.value.value is None
            ):
                offenders.append(f"{path.name}: {node.arg}=")
    assert not offenders, (
        f"the extraction layer sets a grounding status: {offenders}. Grounding is "
        "Milestone 4's stage, with its own artifact under ADR-0003"
    )
