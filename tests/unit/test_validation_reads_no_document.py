"""T073, T070 — this stage holds no document, and never mutates a prior result.

FR-005 cannot be expressed as an import contract: the package legitimately
imports `Span` and `Geometry` from the kernel to carry locations through. So it
is asserted here, structurally — the entry point takes no document, and no module
under `docdoc/validation/` names the type.

The point is not tidiness. If this stage could read the document, it could
compute a location of its own, and then two stages would be able to disagree
about where a value is — with the one holding no offset map being the one that
was wrong.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest
from pydantic import ValidationError as PydanticValidationError

from docdoc.validation import validate
from tests.fixtures.validation import artifacts

MODULES = sorted(pathlib.Path("src/docdoc/validation").rglob("*.py"))


def test_the_scan_is_not_vacuous() -> None:
    assert len(MODULES) >= 10


def test_validate_takes_no_document() -> None:
    parameters = inspect.signature(validate).parameters
    assert list(parameters) == ["extraction", "grounding", "schema", "options"]
    for parameter in parameters.values():
        assert "Document" not in str(parameter.annotation)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_module_names_the_document_type(path: pathlib.Path) -> None:
    """Docstrings and comments are prose and may explain the rule; code may not."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    prose: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                prose.append(first.value.value)
    code = source
    for text in prose:
        code = code.replace(text, "")
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
    assert "Document" not in code, (
        f"{path.name} names Document in code. Every location a finding carries was "
        "computed by Milestone 4 and is copied through; a second path to the same "
        "fact is a second chance to disagree (FR-005)"
    )


def test_a_validation_needs_no_document_at_runtime() -> None:
    pair = artifacts.build()
    result = validate(pair.extraction, pair.grounding, pair.schema)
    assert result.findings == () or result.findings
    # The document object is simply never passed; this asserts the call shape
    # rather than the absence of an attribute.
    with pytest.raises(TypeError):
        validate(pair.extraction, pair.grounding, pair.schema, pair.document)  # type: ignore[misc]


class TestRevalidation:
    """T070 — re-validating produces a new result and leaves the old one alone."""

    def test_both_results_exist_with_their_own_provenance(self) -> None:
        pair = artifacts.build()
        first = validate(pair.extraction, pair.grounding, pair.schema)
        snapshot = first.model_dump()
        second = validate(pair.extraction, pair.grounding, pair.schema)

        assert first.model_dump() == snapshot, "the earlier result was mutated"
        assert second is not first
        assert second.provenance.validator_version == first.provenance.validator_version

    def test_a_result_is_frozen(self) -> None:
        pair = artifacts.build()
        result = validate(pair.extraction, pair.grounding, pair.schema)
        with pytest.raises(PydanticValidationError, match="frozen"):
            result.verdict = "valid"  # type: ignore[misc]

    def test_re_validating_under_new_options_does_not_touch_the_old_result(self) -> None:
        from docdoc.validation import GroundingPolicy, Severity, ValidationOptions

        pair = artifacts.build(ungrounded_total=True)
        lenient = validate(pair.extraction, pair.grounding, pair.schema)
        snapshot = lenient.model_dump()
        strict = validate(
            pair.extraction,
            pair.grounding,
            pair.schema,
            options=ValidationOptions(grounding_policy=GroundingPolicy(ungrounded=Severity.ERROR)),
        )
        assert lenient.model_dump() == snapshot
        assert strict.artifact_id != lenient.artifact_id
        assert strict.verdict is not lenient.verdict
