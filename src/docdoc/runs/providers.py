"""Which adapter answers for a tenant: saved settings, then env, then shipped.

Model and key resolve field by field -- the tenant's row, the deployment
default's, the environment, the adapter's shipped default. The priority list
resolves as a whole: the tenant's, else the default's, else ``--adapter`` or
``DOCDOC_MODEL_ADAPTERS``, else ``DEFAULT_PRIORITY``. A saved key that does not
unseal makes the adapter unavailable; it never falls back to a lower layer's key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from docdoc.extraction.adapter_registry import (
    ADAPTERS_ENV,
    AdapterConfig,
    AdapterRegistry,
    _configured_priority,
    default_adapter_registry,
)
from docdoc.extraction.adapters.gemini import DEFAULT_MODEL, MODEL_ENV
from docdoc.extraction.errors import ModelProviderError
from docdoc.runs.provider_settings import DEFAULT_SCOPE
from docdoc.runs.secrets import SECRETS_KEY_ENV, SecretUnreadable, last4

if TYPE_CHECKING:
    from datetime import datetime

    from docdoc.extraction.adapter import ModelAdapter
    from docdoc.runs.provider_settings import AdapterSettings, ProviderStore, ScopeSettings
    from docdoc.runs.secrets import Sealer

__all__ = [
    "KNOWN_ADAPTERS",
    "Resolved",
    "ResolvedAdapter",
    "Source",
    "registry_for",
    "resolve",
    "resolve_adapter",
    "selected_id",
]

Source = Literal["tenant", "default", "env", "shipped"]

KNOWN_ADAPTERS: tuple[str, ...] = ("gemini", "echo")


@dataclass(frozen=True)
class ResolvedAdapter:
    id: str
    model: str | None
    model_source: Source
    key_last4: str | None
    #: ``"env"`` means the key comes from the environment, or there is none.
    key_source: Source
    config: AdapterConfig
    #: Of the most specific saved row for this adapter; ``None`` when nothing is saved.
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class Resolved:
    priority: tuple[str, ...]
    priority_source: Source
    adapters: tuple[ResolvedAdapter, ...]


def resolve(
    tenant_id: str | None,
    store: ProviderStore | None,
    sealer: Sealer | None,
    *,
    env_priority: tuple[str, ...] | None = None,
) -> Resolved:
    """Store errors propagate: whether to run env-only without the database is the caller's call."""
    scopes: list[tuple[Source, ScopeSettings]] = []
    if store is not None:
        if tenant_id and tenant_id != DEFAULT_SCOPE:
            scopes.append(("tenant", store.scope(tenant_id)))
        scopes.append(("default", store.scope(DEFAULT_SCOPE)))

    saved = next(((s.priority, src) for src, s in scopes if s.priority), None)
    priority: tuple[str, ...]
    priority_source: Source
    if saved is not None:
        priority, priority_source = saved
    elif env_priority:
        priority, priority_source = tuple(env_priority), "env"
    else:
        priority = _configured_priority()
        named = os.environ.get(ADAPTERS_ENV, "").replace(",", "").strip()
        priority_source = "env" if named else "shipped"

    return Resolved(
        priority=priority,
        priority_source=priority_source,
        adapters=tuple(
            _resolve_one(a, [(src, s.adapters[a]) for src, s in scopes if a in s.adapters], sealer)
            for a in KNOWN_ADAPTERS
        ),
    )


def _resolve_one(
    adapter_id: str, rows: list[tuple[Source, AdapterSettings]], sealer: Sealer | None
) -> ResolvedAdapter:
    if adapter_id == "echo":
        # Echo answers from DOCDOC_ECHO_FIXTURES; nothing saved applies to it.
        return ResolvedAdapter("echo", None, "shipped", None, "env", AdapterConfig())

    stamp = rows[0][1] if rows else None
    model_row = next(((src, r.model) for src, r in rows if r.model), None)
    model: str
    model_source: Source
    if model_row is not None:
        model_source, model = model_row
    elif os.environ.get(MODEL_ENV):
        model, model_source = os.environ[MODEL_ENV], "env"
    else:
        model, model_source = DEFAULT_MODEL, "shipped"

    key_row = next(((src, r) for src, r in rows if r.key_ciphertext is not None), None)
    api_key: str | None = None
    unavailable: str | None = None
    key_source: Source
    if key_row is not None:
        key_source, row = key_row
        key_last4 = row.key_last4
        try:
            if sealer is None or row.key_ciphertext is None:
                raise SecretUnreadable
            plaintext = sealer.unseal(row.key_ciphertext)
        except SecretUnreadable:
            unavailable = (
                f"the stored API key for {adapter_id} cannot be decrypted with {SECRETS_KEY_ENV}"
            )
        else:
            # An empty key must not reach the adapter: `None or ""` there means
            # "use the env key", the fallback a saved key must never take.
            if plaintext.strip():
                api_key = plaintext
            else:
                unavailable = f"the stored API key for {adapter_id} is empty"
    else:
        # The same lookup as `GeminiAdapter._resolved_key`, for the last 4 only.
        env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        key_last4, key_source = (last4(env_key) if env_key else None), "env"

    return ResolvedAdapter(
        id=adapter_id,
        model=model,
        model_source=model_source,
        key_last4=key_last4,
        key_source=key_source,
        config=AdapterConfig(model=model, api_key=api_key, unavailable=unavailable),
        updated_at=stamp.updated_at if stamp else None,
        updated_by=stamp.updated_by if stamp else None,
    )


def registry_for(resolved: Resolved) -> AdapterRegistry:
    return default_adapter_registry(
        resolved.priority, configure={a.id: a.config for a in resolved.adapters}
    )


def resolve_adapter(
    tenant_id: str | None,
    store: ProviderStore | None,
    sealer: Sealer | None,
    *,
    env_priority: tuple[str, ...] | None = None,
) -> ModelAdapter:
    """The adapter for a run; raises `ModelProviderError` when none is usable."""
    return registry_for(resolve(tenant_id, store, sealer, env_priority=env_priority)).select()


def selected_id(resolved: Resolved) -> str | None:
    """The adapter that would answer, or ``None`` when none would."""
    try:
        return registry_for(resolved).select().id
    except ModelProviderError:
        return None
