"""T062, T063 — provenance completeness and the grounding boundary.

SC-011: every result records everything needed to explain it, readable without
re-running the extraction. SC-018: every grounding field is unresolved, because
Milestone 4 owns that stage.

The second is the one worth keeping forever. A grounding status set one milestone
early leaves every test green and the ADR-0003 stage boundary broken.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import (
    ExtractionOptions,
    ExtractionProvenance,
    SchemaRegistry,
    extract,
)
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
    return extract(
        make_document(DOCUMENT_TEXT), schema=identity, registry=registry, adapter=echo
    )


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


# -- SC-011: the record is complete ------------------------------------------


def test_provenance_records_every_required_field(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The field set is pinned, so dropping one is a deliberate change."""
    assert set(ExtractionProvenance.model_fields) == {
        "document_id",
        "schema_identity",
        "schema_hash",
        "prompt_hash",
        "projection_id",
        "adapter_id",
        "adapter_version",
        "model_id",
        "model_version",
        "decoding",
        "extractor_version",
        "usage",
    }


def test_no_recorded_field_is_empty(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """A field that is present but blank records nothing while looking complete."""
    provenance = _result(registry, echo).provenance
    for name in ExtractionProvenance.model_fields:
        value = getattr(provenance, name)
        assert value is not None, f"{name} is unrecorded"
        if isinstance(value, str):
            assert value.strip(), f"{name} is blank"


def test_the_recorded_values_are_the_ones_actually_used(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    entry = registry.resolve("invoice@1")
    options = ExtractionOptions(max_tokens=1234, effort="low")
    result = extract(
        make_document(DOCUMENT_TEXT),
        schema="invoice@1",
        registry=registry,
        adapter=echo,
        options=options,
    )
    p = result.provenance
    assert p.document_id == make_document(DOCUMENT_TEXT).id
    assert p.schema_identity == "invoice@1"
    assert p.schema_hash == entry.schema_hash
    assert p.prompt_hash == entry.prompt_hash
    assert p.projection_id == "response-shape@1"
    assert p.adapter_id == echo.id
    assert p.adapter_version == echo.version
    assert p.model_id == echo.model_id
    assert p.model_version == echo.model_version
    assert p.decoding == options, "the options as they actually ran, not the defaults"
    assert p.extractor_version.startswith("1.0.0+")


def test_provenance_is_frozen(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """Provenance MUST NOT be silently overwritten (Principle VIII)."""
    provenance = _result(registry, echo).provenance
    with pytest.raises(Exception, match=r"frozen|immutable"):
        provenance.schema_identity = "invoice@2"  # type: ignore[misc]


def test_usage_is_recorded_even_when_an_adapter_has_no_tokens(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """Absence of a token count is a normal condition, not a missing record."""
    usage = _result(registry, echo).provenance.usage
    assert usage is not None
    assert usage.input_tokens is None


def test_the_result_carries_its_artifact_id(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    assert _result(registry, echo).artifact_id.startswith("sha256:")


# -- SC-018 / EXT-24: the grounding boundary ---------------------------------


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


def test_model_confidence_is_carried_and_labelled_untrusted_in_the_schema(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """FR-031 -- recorded verbatim, and labelled where the label actually travels.

    A comment in the source does not reach a caller. FR-031 requires the untrusted
    label wherever the field is *exposed*, so it lives in the field description and
    therefore in the generated schema -- which is what this asserts.
    """
    assert _result(registry, echo).value_at("total").model_confidence == 0.91

    description = ExtractedValue.model_json_schema()["properties"]["model_confidence"][
        "description"
    ]
    assert "UNTRUSTED" in description
    assert "must not influence any routing" in description


def test_the_grounding_fields_are_not_labelled_untrusted(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The other half of ADR-0004: these two are the trusted ones, once computed."""
    properties = ExtractedValue.model_json_schema()["properties"]
    for name in ("grounding", "grounding_score"):
        assert "UNTRUSTED" not in str(properties[name])


def test_re_extraction_does_not_mutate_the_earlier_result(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """FR-038 -- reprocessing produces a new result; it never rewrites one."""
    document = make_document(DOCUMENT_TEXT)
    first = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    snapshot = (first.artifact_id, first.provenance.model_dump(), first.value_at("total").value)

    second = extract(document, schema="invoice@2", registry=registry, adapter=echo)
    assert second.artifact_id != first.artifact_id
    assert (
        first.artifact_id,
        first.provenance.model_dump(),
        first.value_at("total").value,
    ) == snapshot
