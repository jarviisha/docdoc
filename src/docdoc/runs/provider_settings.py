"""Storage for provider settings per scope: the deployment default or one tenant.

A scope is `DEFAULT_SCOPE` (the empty string) or a tenant id. A missing priority,
model or key means "inherit"; a row that would hold nothing is deleted instead.
Keys are stored sealed; this module never sees a plaintext key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

__all__ = [
    "DEFAULT_SCOPE",
    "KEEP_KEY",
    "AdapterSettings",
    "KeepKey",
    "PostgresProviderStore",
    "ProviderStore",
    "ScopeSettings",
]

DEFAULT_SCOPE = ""


class KeepKey:
    """The type of `KEEP_KEY`: leave the stored key as it is."""


KEEP_KEY: Final = KeepKey()


@dataclass(frozen=True)
class AdapterSettings:
    model: str | None
    key_ciphertext: bytes | None
    key_last4: str | None
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True)
class ScopeSettings:
    priority: tuple[str, ...] | None
    adapters: Mapping[str, AdapterSettings]


class ProviderStore(Protocol):
    def scope(self, tenant_id: str) -> ScopeSettings:
        """What is saved for the scope; empty when nothing is."""
        ...

    def set_priority(
        self, tenant_id: str, priority: tuple[str, ...] | None, *, by: str, now: datetime
    ) -> None:
        """Save the order; ``None`` deletes it."""
        ...

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
        """Upsert; ``key`` is ``(ciphertext, last4)``, ``None`` to clear, or `KEEP_KEY`.

        A row left with no model and no key is deleted.
        """
        ...

    def reset(self, tenant_id: str) -> None:
        """Delete everything saved for a tenant; the default scope is refused."""
        ...


def refuse_default(tenant_id: str) -> None:
    if tenant_id == DEFAULT_SCOPE:
        raise ValueError("the deployment default cannot be reset")


def refuse_empty(priority: tuple[str, ...] | None) -> None:
    if priority is not None and not priority:
        raise ValueError("an empty priority means nothing; use None to inherit")


@dataclass
class PostgresProviderStore:
    """``execute`` is `PostgresRunQueue._execute`, as in the schema store."""

    execute: Callable[..., Any]

    def scope(self, tenant_id: str) -> ScopeSettings:
        p = self.execute(
            "SELECT priority FROM provider_priorities WHERE tenant_id = %s",
            (tenant_id,),
            fetch="one",
        )
        rows = self.execute(
            "SELECT adapter_id, model, key_ciphertext, key_last4, updated_at, updated_by "
            "FROM provider_settings WHERE tenant_id = %s",
            (tenant_id,),
            fetch="all",
        )
        return ScopeSettings(
            priority=tuple(p["priority"]) if p else None,
            adapters={
                r["adapter_id"]: AdapterSettings(
                    model=r["model"],
                    key_ciphertext=None
                    if r["key_ciphertext"] is None
                    else bytes(r["key_ciphertext"]),
                    key_last4=r["key_last4"],
                    updated_at=r["updated_at"],
                    updated_by=r["updated_by"],
                )
                for r in rows or ()
            },
        )

    def set_priority(
        self, tenant_id: str, priority: tuple[str, ...] | None, *, by: str, now: datetime
    ) -> None:
        refuse_empty(priority)
        if priority is None:
            self.execute("DELETE FROM provider_priorities WHERE tenant_id = %s", (tenant_id,))
            return
        self.execute(
            "INSERT INTO provider_priorities (tenant_id, priority, updated_at, updated_by) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id) DO UPDATE SET "
            "priority = EXCLUDED.priority, updated_at = EXCLUDED.updated_at, "
            "updated_by = EXCLUDED.updated_by",
            (tenant_id, list(priority), now, by),
        )

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
        if model is None and (key is None or isinstance(key, KeepKey)):
            # Nothing would be left to inherit from: delete, or for KEEP_KEY keep
            # only a row that still holds a key. One statement, so it is atomic.
            gone = "WHERE tenant_id = %s AND adapter_id = %s"
            if key is None:
                self.execute(f"DELETE FROM provider_settings {gone}", (tenant_id, adapter_id))
            else:
                self.execute(
                    f"WITH d AS (DELETE FROM provider_settings {gone} AND key_ciphertext IS NULL "
                    "RETURNING 1) UPDATE provider_settings SET model = NULL, updated_at = %s, "
                    "updated_by = %s WHERE tenant_id = %s AND adapter_id = %s "
                    "AND key_ciphertext IS NOT NULL",
                    (tenant_id, adapter_id, now, by, tenant_id, adapter_id),
                )
            return
        if isinstance(key, KeepKey):
            self.execute(
                "INSERT INTO provider_settings (tenant_id, adapter_id, model, updated_at, "
                "updated_by) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tenant_id, adapter_id) "
                "DO UPDATE SET model = EXCLUDED.model, updated_at = EXCLUDED.updated_at, "
                "updated_by = EXCLUDED.updated_by",
                (tenant_id, adapter_id, model, now, by),
            )
        else:
            ciphertext, last4 = key if key else (None, None)
            self.execute(
                "INSERT INTO provider_settings (tenant_id, adapter_id, model, key_ciphertext, "
                "key_last4, updated_at, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, adapter_id) DO UPDATE SET model = EXCLUDED.model, "
                "key_ciphertext = EXCLUDED.key_ciphertext, key_last4 = EXCLUDED.key_last4, "
                "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
                (tenant_id, adapter_id, model, ciphertext, last4, now, by),
            )

    def reset(self, tenant_id: str) -> None:
        refuse_default(tenant_id)
        for table in ("provider_priorities", "provider_settings"):
            # `table` comes from the literal tuple above and nowhere else.
            self.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
