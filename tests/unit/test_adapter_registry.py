"""T082 — choosing an adapter without naming one (FR-021, FR-028, US3/AC2).

The requirement is that application code never names a provider. Before this
existed, every documented example wrote `adapter=GeminiAdapter()` — literally the
thing FR-021 forbids — while the contract claimed the opposite. These tests pin
the mechanism that closes it, and one property that matters more than the rest.

**The echo adapter must never be selected automatically.** It answers from canned
fixtures. Auto-selecting it would turn a missing credential into a stream of
confident, fabricated extractions, which is the worst failure this layer could
produce: not an error, but plausible wrong data carrying full provenance.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import (
    Availability,
    ExtractionOptions,
    ModelProviderError,
    ModelResponse,
    ModelUsage,
    SchemaRegistry,
    default_adapter,
    default_adapter_registry,
    extract,
)
from docdoc.extraction.adapter_registry import AdapterRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from tests.support import make_document


class _Stub:
    """A minimal adapter, so the registry is tested and not one provider."""

    def __init__(self, adapter_id: str, *, usable: bool = True, reason: str | None = None) -> None:
        self._id = adapter_id
        self._usable = usable
        self._reason = reason

    @property
    def id(self) -> str:
        return self._id

    @property
    def version(self) -> str:
        return "1.0.0"

    def available(self) -> Availability:
        return Availability(usable=self._usable, reason=self._reason)

    def complete(self, request: Any, options: Any) -> ModelResponse:
        return ModelResponse(payload={}, model_id=self._id, model_version="1", usage=ModelUsage())


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in the developer's environment must not change what these assert."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


# -- selection ---------------------------------------------------------------


def test_the_first_usable_adapter_in_priority_order_is_selected() -> None:
    registry = AdapterRegistry(priority=("second", "first"))
    registry.register(_Stub("first"))
    registry.register(_Stub("second"))
    assert registry.select().id == "second", "configured priority decides, not registration order"


def test_an_unusable_adapter_is_skipped_not_selected() -> None:
    registry = AdapterRegistry(priority=("broken", "working"))
    registry.register(_Stub("broken", usable=False, reason="no credential"))
    registry.register(_Stub("working"))
    assert registry.select().id == "working"


def test_selection_is_deterministic_regardless_of_registration_order() -> None:
    """FR-028's sibling: a registry that depends on insertion order is not configuration."""
    first = AdapterRegistry(priority=())
    for name in ("charlie", "alpha", "bravo"):
        first.register(_Stub(name))
    second = AdapterRegistry(priority=())
    for name in ("bravo", "charlie", "alpha"):
        second.register(_Stub(name))
    assert first.select().id == second.select().id == "alpha", "id breaks the tie"


# -- the property that matters most ------------------------------------------


def test_the_echo_adapter_is_never_selected_automatically() -> None:
    """The worst failure this layer could have, prevented structurally.

    Echo answers from fixtures. If it were selected when no real adapter is
    usable, a missing credential would produce confident, fabricated extractions
    carrying full provenance — not an error, which is far worse than one.
    """
    registry = AdapterRegistry(priority=("echo",))
    registry.register(EchoAdapter.from_fixtures("tests/fixtures/echo"))
    assert len(registry) == 1
    assert registry.candidates()[0].available is True, "it is usable — just not selectable"

    with pytest.raises(ModelProviderError) as caught:
        registry.select()
    assert caught.value.reason == "unavailable"
    assert "never selected automatically" in str(caught.value)


def test_echo_is_still_usable_when_passed_explicitly() -> None:
    """Refusing to auto-select must not make the offline path unreachable."""
    registry = SchemaRegistry.from_paths(["schemas"])
    result = extract(
        make_document("ACME LTD\nINV-001\n"),
        schema="invoice@1",
        registry=registry,
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )
    assert result.provenance.adapter_id == "echo"


def test_a_real_adapter_wins_even_when_echo_is_registered_first() -> None:
    registry = AdapterRegistry(priority=("echo", "gemini"))
    registry.register(EchoAdapter.from_fixtures("tests/fixtures/echo"))
    registry.register(_Stub("gemini"))
    assert registry.select().id == "gemini"


# -- the failure, and what it says -------------------------------------------


def test_no_usable_adapter_names_every_candidate_and_its_reason() -> None:
    """FR-028 — "why not?" is answerable without reading docdoc's source."""
    registry = AdapterRegistry(priority=("alpha", "bravo"))
    registry.register(_Stub("alpha", usable=False, reason="no credential configured"))
    registry.register_unavailable("bravo", reason="extra not installed")

    with pytest.raises(ModelProviderError) as caught:
        registry.select()
    message = str(caught.value)
    assert "alpha: no credential configured" in message
    assert "bravo: extra not installed" in message


def test_an_empty_registry_says_what_to_install() -> None:
    with pytest.raises(ModelProviderError, match=r"docdoc\[google\]"):
        AdapterRegistry().select()


def test_an_uninstalled_extra_is_recorded_not_omitted() -> None:
    """Omitting it would make the error unable to say what to install."""
    registry = AdapterRegistry()
    registry.register_unavailable("gemini", reason="extra not installed")
    assert "gemini" in registry
    assert registry.candidates()[0].adapter is None


# -- the default registry ----------------------------------------------------


def test_the_default_registry_knows_the_real_adapter_and_not_echo() -> None:
    registry = default_adapter_registry()
    ids = [c.id for c in registry.candidates()]
    assert "gemini" in ids
    assert "echo" not in ids, "a fixture adapter must not be in the default set at all"


def test_the_default_registry_reports_the_missing_credential_rather_than_hiding_it() -> None:
    candidate = next(c for c in default_adapter_registry().candidates() if c.id == "gemini")
    assert candidate.available is False
    assert "API key" in (candidate.reason or "")


def test_default_adapter_raises_with_the_reason_when_nothing_is_usable() -> None:
    with pytest.raises(ModelProviderError) as caught:
        default_adapter()
    assert "GEMINI_API_KEY" in str(caught.value)


def test_default_adapter_selects_when_a_credential_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used-for-a-call")
    adapter = default_adapter()
    assert adapter.id == "gemini"


# -- FR-021 as a structural assertion ----------------------------------------


def test_application_code_can_extract_without_naming_a_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US3/AC2 — the whole point.

    These four lines are the entire application-side surface, and none of them
    contains a provider name. Swapping providers changes what is installed and the
    priority order, not this code.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used-for-a-call")

    source = """
from docdoc.extraction import SchemaRegistry, default_adapter, extract

registry = SchemaRegistry.from_paths(["schemas"])
adapter = default_adapter()
""".strip()

    for provider in ("gemini", "Gemini", "google", "anthropic", "openai", "claude", "gpt"):
        assert provider.lower() not in source.lower(), (
            f"{provider!r} appears in what application code must write (FR-021)"
        )

    namespace: dict[str, Any] = {}
    exec(compile(source, "<application code>", "exec"), namespace)
    assert namespace["adapter"].id == "gemini", "configuration chose it; the code above did not"


def test_the_documented_examples_do_not_name_a_provider() -> None:
    """The documentation half of FR-021, which is where it was violated.

    README and the quickstart both wrote `GeminiAdapter()` before T082. This is
    the check that stops it coming back — a requirement about what callers write
    is only met if what we *show* callers meets it.
    """
    import pathlib

    for path in (
        pathlib.Path("README.md"),
        pathlib.Path("specs/003-schema-driven-extraction/quickstart.md"),
        pathlib.Path("docs/concepts/extraction.md"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "GeminiAdapter()" not in text, (
            f"{path} shows application code constructing a provider adapter, which is "
            "exactly what FR-021 forbids. Use default_adapter()"
        )


def test_extract_still_accepts_an_explicit_adapter() -> None:
    """Configuration is the default, not a cage. An explicit adapter still works,
    which is what makes the offline path and the tests possible."""
    registry = SchemaRegistry.from_paths(["schemas"])
    result = extract(
        make_document("ACME LTD\nINV-001\n"),
        schema="invoice@1",
        registry=registry,
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        options=ExtractionOptions(),
    )
    assert result.artifact_id.startswith("sha256:")
