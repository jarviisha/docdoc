"""T033 — the contract every ``ModelAdapter`` must satisfy.

A contract test with one implementation is a description of that implementation.
So this runs against **two** independent ones: ``EchoAdapter``, which the library
ships, and ``MinimalAdapter`` below, written from the protocol alone with none of
Echo's fixture machinery. Where they agree, the contract is real; where only Echo
passed, the assertion was about Echo.

Three implementations run here: ``EchoAdapter``, which the library ships; the
test-local ``MinimalAdapter``, written from the protocol alone; and
``GeminiAdapter``, the one that answers in production.

The third was missing until a convergence pass found it. This file's docstring
had promised "the Anthropic adapter joins this list at Phase 5"; Phase 5 built
the adapter, renamed it to Gemini, and left the list alone. So the contract every
adapter must satisfy had never been run against the adapter that produces every
real extraction. The recorded-response tests in ``test_gemini_mapping.py`` cover
it, but those assert that *our code reads a recorded shape* -- a different claim
from *the adapter honours the contract*.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from docdoc.extraction import (
    Availability,
    ExtractionOptions,
    ModelAdapter,
    ModelResponse,
    ModelUsage,
    SchemaRegistry,
    response_shape_for,
)
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.extraction.adapters.gemini import GeminiAdapter
from docdoc.extraction.conform import conform
from docdoc.extraction.prompt import ModelRequest, build_request

# SC-013: the offline suite must pass on a base install — one with no provider
# SDK at all. Constitution XII: "Provider adapters MUST have integration tests;
# those tests MUST NOT be required to run the unit and property suites." These
# tests exercise the shipped model adapters, so they skip rather than fail when it is absent.
pytest.importorskip("google.genai")

SCHEMAS = pathlib.Path("schemas")


class MinimalAdapter:
    """A second implementation, written from the protocol and nothing else.

    It answers every field with an explicit absence, which is a legitimate result
    (FR-005) and happens to be the smallest response that can satisfy the shape.
    """

    def __init__(self) -> None:
        self.calls = 0

    @property
    def id(self) -> str:
        return "minimal"

    @property
    def version(self) -> str:
        return "0.1.0"

    def available(self) -> Availability:
        return Availability(usable=True)

    def complete(self, request: ModelRequest, options: ExtractionOptions) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            payload=_absent_everywhere(request.response_shape),
            model_id="minimal-null",
            model_version="1",
            usage=ModelUsage(input_tokens=1, output_tokens=1),
        )


def _absent_everywhere(shape: dict[str, Any]) -> Any:
    """Build the smallest payload that satisfies a response shape."""
    kind = shape.get("type")
    if kind == "array":
        return []
    properties: dict[str, Any] = shape.get("properties", {})
    if {"value", "claimed_text"} <= set(properties):
        return {"value": None, "claimed_text": None, "confidence": None}
    return {name: _absent_everywhere(sub) for name, sub in properties.items()}


class _SchemaAwareClient:
    """A stand-in transport that answers whatever schema it is asked for.

    The suite asserts conformance for *every* registered schema, so a client
    replaying one recorded fixture would satisfy only `invoice@1`. This builds a
    minimal conforming payload from the response shape the adapter actually sent,
    which is also the stronger test: the adapter must have put a usable schema on
    the wire for this to work at all.
    """

    def __init__(self) -> None:
        self.models = self

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        payload = _absent_everywhere(config.response_json_schema)

        class _Candidate:
            finish_reason = "STOP"

        class _Feedback:
            block_reason = None

        class _Usage:
            prompt_token_count = 100
            candidates_token_count = 20
            cached_content_token_count = 0
            thoughts_token_count = 0

        class _Response:
            text = json.dumps(payload)
            candidates = (_Candidate(),)
            prompt_feedback = _Feedback()
            usage_metadata = _Usage()
            model_version = model

        return _Response()


ADAPTERS: list[tuple[str, Any]] = [
    ("echo", lambda: EchoAdapter.from_fixtures("tests/fixtures/echo")),
    ("minimal", MinimalAdapter),
    ("gemini", lambda: GeminiAdapter(client=_SchemaAwareClient())),
]


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


def _request(registry: SchemaRegistry, identity: str = "invoice@1") -> ModelRequest:
    entry = registry.resolve(identity)
    return build_request(entry, "a document", response_shape=response_shape_for(entry.schema))


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_an_adapter_satisfies_the_protocol(name: str, factory: Any) -> None:
    """Structural, not nominal -- an adapter need not inherit anything."""
    assert isinstance(factory(), ModelAdapter)


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_identity_and_version_are_stable_strings(name: str, factory: Any) -> None:
    """FR-036 -- a version that is not stable cannot be recorded in provenance."""
    adapter = factory()
    assert isinstance(adapter.id, str)
    assert adapter.id
    assert isinstance(adapter.version, str)
    assert adapter.version
    assert adapter.id == factory().id, "id must not vary between instances"


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_availability_is_reported_not_implied(name: str, factory: Any) -> None:
    """FR-028 -- an unusable adapter says so with a reason, rather than vanishing."""
    availability = factory().available()
    assert isinstance(availability, Availability)
    if not availability.usable:
        assert availability.reason


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_complete_returns_exactly_one_response(
    name: str, factory: Any, registry: SchemaRegistry
) -> None:
    response = factory().complete(_request(registry), ExtractionOptions())
    assert isinstance(response, ModelResponse)
    assert isinstance(response.payload, dict)
    assert response.model_id
    assert response.model_version


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_the_response_conforms_to_the_schema_it_was_asked_for(
    name: str, factory: Any, registry: SchemaRegistry
) -> None:
    """EXT-15…EXT-18 -- the substance of the contract.

    Any adapter's answer must survive conformance for every registered schema. An
    adapter that only works for the one schema its fixtures happen to cover is not
    satisfying a contract.
    """
    for identity in registry.identities():
        entry = registry.resolve(identity)
        response = factory().complete(_request(registry, identity), ExtractionOptions())
        report = conform(response.payload, entry.schema, adapter_id=name)
        assert set(report.values) == {f.name for f in entry.schema.fields}
        assert report.discarded == ()


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_usage_is_optional_but_well_formed(
    name: str, factory: Any, registry: SchemaRegistry
) -> None:
    """An adapter with no notion of tokens reports none -- a normal condition."""
    usage = factory().complete(_request(registry), ExtractionOptions()).usage
    assert isinstance(usage, ModelUsage)
    for field in (usage.input_tokens, usage.output_tokens):
        assert field is None or field >= 0


@pytest.mark.parametrize(("name", "factory"), ADAPTERS, ids=[n for n, _ in ADAPTERS])
def test_completing_twice_does_not_mutate_the_request(
    name: str, factory: Any, registry: SchemaRegistry
) -> None:
    """An adapter that rewrites its request breaks the cached prefix silently."""
    request = _request(registry)
    before = (request.prefix, request.document_text, request.schema_identity)
    adapter = factory()
    adapter.complete(request, ExtractionOptions())
    adapter.complete(request, ExtractionOptions())
    assert (request.prefix, request.document_text, request.schema_identity) == before


def test_the_contract_runs_against_more_than_one_implementation() -> None:
    """Guards the guard: a single-adapter run proves nothing about the contract."""
    assert len(ADAPTERS) >= 2


def test_every_shipped_adapter_is_in_the_suite() -> None:
    """The assertion that would have caught what this file's docstring described.

    A module under ``adapters/`` that the suite does not exercise is an adapter
    nobody has held to the contract. Listing them by directory rather than by hand
    means a fourth adapter fails this until someone adds it.
    """
    import docdoc.extraction.adapters

    shipped = {
        path.stem
        for path in pathlib.Path(docdoc.extraction.adapters.__file__).parent.glob("*.py")
        if path.stem != "__init__"
    }
    covered = {name for name, _ in ADAPTERS}
    assert shipped <= covered, (
        f"these adapters ship but are not held to the contract: {sorted(shipped - covered)}"
    )
