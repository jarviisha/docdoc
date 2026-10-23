"""Resolving the model adapter per tenant: saved settings, then env, then shipped."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from docdoc.extraction.adapter_registry import ADAPTERS_ENV, ECHO_FIXTURES_ENV, _configured_priority
from docdoc.extraction.adapters.gemini import DEFAULT_MODEL, MODEL_ENV, GeminiAdapter
from docdoc.extraction.errors import ModelProviderError
from docdoc.runs.provider_settings import DEFAULT_SCOPE
from docdoc.runs.providers import registry_for, resolve, resolve_adapter, selected_id
from docdoc.runs.secrets import Sealer
from tests.fixtures.provider_store import InMemoryProviderStore

NOW = datetime(2026, 10, 23, tzinfo=UTC)
UNREADABLE = "the stored API key for gemini cannot be decrypted with DOCDOC_SECRETS_KEY"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", MODEL_ENV, ADAPTERS_ENV, ECHO_FIXTURES_ENV):
        monkeypatch.delenv(name, raising=False)


def _sealer() -> Sealer:
    return Sealer(Fernet(Fernet.generate_key()))


def _save(
    store: InMemoryProviderStore,
    sealer: Sealer,
    scope: str,
    *,
    model: str | None = None,
    key: str | None = None,
) -> None:
    store.set_adapter(
        scope,
        "gemini",
        model=model,
        key=(sealer.seal(key), key[-4:]) if key else None,
        by="ops",
        now=NOW,
    )


def _gemini(resolved):  # type: ignore[no-untyped-def]
    return next(a for a in resolved.adapters if a.id == "gemini")


def test_nothing_saved_matches_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-env-key-ABCD")
    resolved = resolve("acme", InMemoryProviderStore(), _sealer())

    assert resolved.priority == _configured_priority()
    assert resolved.priority_source == "shipped"
    gemini = _gemini(resolved)
    assert (gemini.model, gemini.model_source) == (DEFAULT_MODEL, "shipped")
    assert (gemini.key_last4, gemini.key_source) == ("ABCD", "env")
    assert gemini.updated_at is None
    assert [a.id for a in resolved.adapters] == ["gemini", "echo"]
    assert resolve_adapter("acme", InMemoryProviderStore(), _sealer()).id == "gemini"

    monkeypatch.setenv(MODEL_ENV, "gemini-env-model")
    monkeypatch.setenv(ADAPTERS_ENV, "gemini")
    resolved = resolve("acme", InMemoryProviderStore(), _sealer())
    assert resolved.priority_source == "env"
    assert (_gemini(resolved).model, _gemini(resolved).model_source) == (
        "gemini-env-model",
        "env",
    )


def test_tenant_model_overrides_default() -> None:
    store, sealer = InMemoryProviderStore(), _sealer()
    _save(store, sealer, DEFAULT_SCOPE, model="gemini-default")
    _save(store, sealer, "acme", model="gemini-tenant")

    assert _gemini(resolve("acme", store, sealer)).model == "gemini-tenant"
    assert _gemini(resolve("acme", store, sealer)).model_source == "tenant"
    assert _gemini(resolve("other", store, sealer)).model == "gemini-default"
    assert _gemini(resolve(None, store, sealer)).model_source == "default"


def test_partial_override_inherits_key() -> None:
    store, sealer = InMemoryProviderStore(), _sealer()
    _save(store, sealer, DEFAULT_SCOPE, key="fake-default-key-WXYZ")
    _save(store, sealer, "acme", model="gemini-tenant")

    gemini = _gemini(resolve("acme", store, sealer))
    assert (gemini.model, gemini.model_source) == ("gemini-tenant", "tenant")
    assert (gemini.key_last4, gemini.key_source) == ("WXYZ", "default")
    assert gemini.config.api_key == "fake-default-key-WXYZ"
    assert gemini.updated_by == "ops"


def test_priority_whole_list_tenant_then_default_then_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, sealer = InMemoryProviderStore(), _sealer()
    assert resolve("acme", store, sealer, env_priority=("echo",)).priority == ("echo",)
    assert resolve("acme", store, sealer, env_priority=("echo",)).priority_source == "env"

    store.set_priority(DEFAULT_SCOPE, ("gemini",), by="ops", now=NOW)
    resolved = resolve("acme", store, sealer, env_priority=("echo",))
    assert (resolved.priority, resolved.priority_source) == (("gemini",), "default")

    store.set_priority("acme", ("echo", "gemini"), by="ops", now=NOW)
    resolved = resolve("acme", store, sealer, env_priority=("echo",))
    assert (resolved.priority, resolved.priority_source) == (("echo", "gemini"), "tenant")


def test_unreadable_inherited_key_is_unavailable_not_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-env-key-ABCD")
    store = InMemoryProviderStore()
    _save(store, _sealer(), DEFAULT_SCOPE, key="fake-default-key-WXYZ")

    for sealer in (_sealer(), None):
        resolved = resolve("acme", store, sealer)
        (gemini,) = [c for c in registry_for(resolved).candidates() if c.id == "gemini"]
        assert (gemini.available, gemini.reason) == (False, UNREADABLE)
        assert selected_id(resolved) is None
        with pytest.raises(ModelProviderError):
            resolve_adapter("acme", store, sealer)


def test_echo_only_when_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ECHO_FIXTURES_ENV, "tests/fixtures/echo")
    store, sealer = InMemoryProviderStore(), _sealer()
    assert selected_id(resolve("acme", store, sealer)) is None

    store.set_priority("acme", ("echo",), by="ops", now=NOW)
    assert resolve_adapter("acme", store, sealer).id == "echo"
    assert selected_id(resolve("other", store, sealer)) is None


def test_no_db_is_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-env-key-EFGH")
    resolved = resolve("acme", None, None, env_priority=("gemini",))

    assert (resolved.priority, resolved.priority_source) == (("gemini",), "env")
    assert (_gemini(resolved).key_last4, _gemini(resolved).key_source) == ("EFGH", "env")
    assert _gemini(resolved).config.api_key is None
    assert selected_id(resolved) == "gemini"


def test_gemini_probe_reports_provider_error_without_raising() -> None:
    def refuse(**kwargs: object) -> None:
        raise RuntimeError("404 NOT_FOUND. model gemini-nope is not found")

    calls: list[dict[str, object]] = []

    def fetch(**kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    broken = GeminiAdapter(
        model="gemini-nope", client=SimpleNamespace(models=SimpleNamespace(get=refuse))
    )
    result = broken.probe(timeout_s=1.0)
    assert not result.usable
    assert "model gemini-nope is not found" in (result.reason or "")

    fine = GeminiAdapter(
        model="gemini-ok", client=SimpleNamespace(models=SimpleNamespace(get=fetch))
    )
    assert fine.probe(timeout_s=1.5).usable
    assert calls[0]["model"] == "gemini-ok"
    assert calls[0]["config"].http_options.timeout == 1500  # type: ignore[attr-defined]


def test_gemini_probe_without_key_is_the_availability_answer() -> None:
    result = GeminiAdapter().probe()
    assert not result.usable
    assert "no API key" in (result.reason or "")
