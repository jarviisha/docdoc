"""An in-memory `ProviderStore`, with the Postgres store's semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.runs.provider_settings import AdapterSettings, KeepKey, ScopeSettings, refuse_default

if TYPE_CHECKING:
    from datetime import datetime


class InMemoryProviderStore:
    def __init__(self) -> None:
        self._priority: dict[str, tuple[str, ...]] = {}
        self._adapters: dict[tuple[str, str], AdapterSettings] = {}

    def scope(self, tenant_id: str) -> ScopeSettings:
        return ScopeSettings(
            priority=self._priority.get(tenant_id),
            adapters={a: s for (t, a), s in self._adapters.items() if t == tenant_id},
        )

    def set_priority(
        self, tenant_id: str, priority: tuple[str, ...] | None, *, by: str, now: datetime
    ) -> None:
        if priority is None:
            self._priority.pop(tenant_id, None)
        else:
            self._priority[tenant_id] = tuple(priority)

    def set_adapter(
        self,
        tenant_id: str,
        adapter_id: str,
        *,
        model: str | None,
        key: tuple[bytes, str] | KeepKey | None,
        by: str,
        now: datetime,
    ) -> None:
        old = self._adapters.get((tenant_id, adapter_id))
        if isinstance(key, KeepKey):
            ciphertext, last4 = (old.key_ciphertext, old.key_last4) if old else (None, None)
        else:
            ciphertext, last4 = key if key else (None, None)
        if model is None and ciphertext is None:
            self._adapters.pop((tenant_id, adapter_id), None)
        else:
            self._adapters[(tenant_id, adapter_id)] = AdapterSettings(
                model, ciphertext, last4, now, by
            )

    def reset(self, tenant_id: str) -> None:
        refuse_default(tenant_id)
        self._priority.pop(tenant_id, None)
        self._adapters = {k: v for k, v in self._adapters.items() if k[0] != tenant_id}
