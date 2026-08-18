"""The deterministic in-repo adapter.

This is a **library deliverable, not a test double**. Without it, nobody could
run the extraction path without credentials, every test would cost money and vary
run to run, and the documented example would be unreachable for anyone who has
not signed up for a provider (FR-044, SC-001, SC-019).

It satisfies the same contract as the real adapter, and the contract suite runs
against both. That is the point: a contract test with one implementation is a
description of that implementation.

The failure constructors exist for the same reason. ``malformed()`` and
``refusing()`` let the failure paths be exercised offline, so "what happens when
the model returns the wrong shape" is a test rather than a hope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docdoc.extraction.adapter import (
    Availability,
    ExtractionOptions,
    ModelResponse,
    ModelUsage,
)
from docdoc.extraction.errors import ModelProviderError

if TYPE_CHECKING:
    from docdoc.extraction.prompt import ModelRequest

__all__ = ["EchoAdapter"]

_ADAPTER_ID = "echo"
_ADAPTER_VERSION = "1.0.0"
_MODEL_ID = "echo-fixture"
_MODEL_VERSION = "1"


class EchoAdapter:
    """Returns canned responses, keyed by the schema identity in the request.

    Deterministic by construction: no clock, no randomness, no network. Two runs
    over the same fixtures produce byte-identical results, which is what lets the
    identity tests assert equality rather than approximate it.
    """

    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | None = None,
        *,
        mode: str = "ok",
        reason: str = "refusal",
        version: str = _ADAPTER_VERSION,
        model_id: str = _MODEL_ID,
        model_version: str = _MODEL_VERSION,
    ) -> None:
        self._responses = dict(responses or {})
        self._mode = mode
        self._reason = reason
        self._version = version
        self._model_id = model_id
        self._model_version = model_version

    # -- construction -------------------------------------------------------

    @classmethod
    def from_fixtures(cls, path: Path | str) -> EchoAdapter:
        """Load one JSON file per schema identity from ``path``.

        The file name is the identity: ``invoice@1.json`` answers requests for
        ``invoice@1``.
        """
        root = Path(path)
        responses: dict[str, dict[str, Any]] = {}
        for file in sorted(root.glob("*.json")):
            responses[file.stem] = json.loads(file.read_text(encoding="utf-8"))
        return cls(responses)

    @classmethod
    def returning(cls, identity: str, payload: dict[str, Any]) -> EchoAdapter:
        """One canned response, for a test that does not need a fixture file."""
        return cls({identity: payload})

    @classmethod
    def malformed(cls) -> EchoAdapter:
        """Answers with a shape the schema did not ask for."""
        return cls(mode="malformed")

    @classmethod
    def refusing(cls, *, category: str = "unspecified") -> EchoAdapter:
        """Declines on content grounds, the way a real provider does.

        A refusal is not an exception on the wire -- it is a *successful*
        response whose stop reason says the model declined. Reproducing that
        offline is the only way the branch that checks the stop reason before
        reading content gets tested without credentials.
        """
        return cls(mode="refusal", reason=category)

    @classmethod
    def failing(cls, *, reason: str = "service") -> EchoAdapter:
        """Fails the way a provider fails, for the retry-classification tests."""
        return cls(mode="provider_error", reason=reason)

    # -- the contract -------------------------------------------------------

    @property
    def id(self) -> str:
        return _ADAPTER_ID

    @property
    def version(self) -> str:
        return self._version

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_version(self) -> str:
        return self._model_version

    def available(self) -> Availability:
        return Availability(usable=True)

    def complete(self, request: ModelRequest, options: ExtractionOptions) -> ModelResponse:
        if self._mode == "refusal":
            raise ModelProviderError(
                "the model declined to answer on content grounds",
                reason="refusal",
                adapter_id=_ADAPTER_ID,
                refusal_category=self._reason,
                attempts=1,
            )
        if self._mode == "provider_error":
            raise ModelProviderError(
                f"simulated {self._reason} failure",
                reason=self._reason,
                adapter_id=_ADAPTER_ID,
                attempts=1,
            )
        payload: dict[str, Any]
        if self._mode == "malformed":
            payload = {"not_a_declared_field": "and the wrong shape entirely"}
        else:
            payload = self._lookup(request)
        return ModelResponse(
            payload=payload,
            model_id=self._model_id,
            model_version=self._model_version,
            usage=ModelUsage(),
        )

    def _lookup(self, request: ModelRequest) -> dict[str, Any]:
        identity = request.schema_identity
        if identity in self._responses:
            return self._responses[identity]
        raise ModelProviderError(
            f"the echo adapter has no canned response for {identity!r}; "
            f"it holds {sorted(self._responses) or '(none)'}",
            reason="request",
            adapter_id=_ADAPTER_ID,
            schema_identity=identity,
        )
