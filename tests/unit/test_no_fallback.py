"""T080 — no silent fallback (FR-029, SC-012).

Principle VIII's eighth word is "forbidden", and this is the requirement the
analysis pass found had no test at all. The gap mattered: a fallback is the kind of
convenience that gets added to make a flaky test pass, and every result produced
after it would name a model that did not answer.

The assertions below run against a registry holding **two** adapters and **two**
schema majors, so a fallback would have somewhere to go if the code permitted one.
A test with one candidate cannot distinguish "did not fall back" from "had nowhere
to fall".
"""

from __future__ import annotations

import pytest

from docdoc.extraction import (
    ModelProviderError,
    SchemaError,
    SchemaRegistry,
    extract,
)
from docdoc.extraction.adapters.echo import EchoAdapter
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\nTotal 1,240.00\n"


@pytest.fixture
def registry() -> SchemaRegistry:
    """Holds invoice@1, invoice@2, and receipt@1 -- somewhere to fall back to."""
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def working() -> EchoAdapter:
    """A perfectly good adapter, available the whole time as a temptation."""
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


class UnavailableAdapter(EchoAdapter):
    """Installed but unusable -- the missing-credential shape (FR-028)."""

    def available(self):  # type: ignore[no-untyped-def]
        from docdoc.extraction import Availability

        return Availability(usable=False, reason="no credential configured")


@pytest.mark.parametrize(
    ("label", "factory"),
    [
        ("permanent", lambda: EchoAdapter.failing(reason="auth")),
        ("refusal", EchoAdapter.refusing),
        ("transient-exhausted", lambda: EchoAdapter.failing(reason="service")),
        ("unavailable", UnavailableAdapter),
    ],
)
def test_a_failing_adapter_is_not_replaced_by_a_working_one(
    registry: SchemaRegistry, working: EchoAdapter, label: str, factory: object
) -> None:
    """The failure surfaces. It does not become someone else's answer."""
    failing = factory()  # type: ignore[operator]
    with pytest.raises(ModelProviderError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=failing,
        )
    assert caught.value.adapter_id == failing.id, "the error names the adapter that failed"


def test_a_failing_adapter_does_not_fall_back_to_another_schema_version(
    registry: SchemaRegistry,
) -> None:
    """A registry with two majors, and a response fixture for only one of them.

    The temptation here is concrete: ``invoice@2`` would have answered. Asking for
    ``invoice@1`` from an adapter that has no ``invoice@1`` fixture must fail rather
    than quietly serve the other major's answer.
    """
    import json
    import pathlib

    only_v2 = EchoAdapter(
        {"invoice@2": json.loads(pathlib.Path("tests/fixtures/echo/invoice@2.json").read_text())}
    )
    with pytest.raises(ModelProviderError) as caught:
        extract(
            make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=only_v2
        )
    assert caught.value.schema_identity == "invoice@1"
    assert "invoice@2" in str(caught.value), "the error may name what it has, not silently use it"


def test_an_unknown_schema_does_not_resolve_to_a_neighbour(
    registry: SchemaRegistry, working: EchoAdapter
) -> None:
    """The schema half of the same rule (FR-016)."""
    with pytest.raises(SchemaError) as caught:
        extract(
            make_document(DOCUMENT_TEXT), schema="invoice@3", registry=registry, adapter=working
        )
    assert caught.value.available == ("invoice@1", "invoice@2")


def test_extract_takes_one_adapter_and_has_no_room_for_a_second() -> None:
    """The structural guarantee behind the behavioural ones.

    ``extract()`` accepts a single adapter, not a list and not a registry of them,
    so there is no argument a fallback could be threaded through. That is a
    stronger statement than any of the assertions above, and it is the reason they
    hold.
    """
    import inspect

    parameters = inspect.signature(extract).parameters
    assert "adapter" in parameters
    assert not {"adapters", "fallback", "fallbacks", "adapter_chain"} & set(parameters)


def test_no_module_in_the_layer_catches_a_provider_error_to_retry_elsewhere() -> None:
    """A fallback would have to swallow the failure somewhere; this looks for that.

    ``extract()`` catches ``ModelProviderError`` in order to log the event -- and
    re-raises it. Any handler that does *not* re-raise is either a fallback or a
    swallowed failure, and both are forbidden.
    """
    import ast
    import pathlib

    import docdoc.extraction

    root = pathlib.Path(docdoc.extraction.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = ast.dump(node.type) if node.type else ""
            if "ProviderError" not in names and "ExtractionError" not in names:
                continue
            reraises = any(
                isinstance(inner, ast.Raise) for inner in ast.walk(ast.Module(node.body, []))
            )
            if not reraises:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"a handler swallows a provider or extraction failure at {offenders}. "
        "Every such failure must surface; a caught-and-continued one is a fallback"
    )
