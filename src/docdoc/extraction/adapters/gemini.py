"""The Google Gemini adapter -- the one place a provider SDK may be imported.

Everything provider-shaped stops here. Above this module the extraction layer
knows about ``ModelRequest``, ``ModelResponse``, and docdoc's own errors; it does
not know that Gemini exists (FR-021, FR-023).

Three things about this provider are load-bearing enough to state at the top.

**A refusal is a successful response.** The HTTP call returns 200 and the refusal
is a ``finish_reason`` on the candidate. Code that reads ``response.text``
unconditionally reports a refusal as an answer, so this adapter branches on the
finish reason *before* touching content. Gemini splits refusal into more
categories than one, and they do not mean the same thing -- see ``_REFUSALS``.

**The schema goes in ``response_json_schema``, not ``response_schema``.** The
latter takes the SDK's own ``Schema`` type (an OpenAPI 3.0 subset); the former
takes standard JSON Schema, which is what the projection produces. Using the
wrong one would mean hand-translating the projection into a second schema dialect
for no reason.

**Reasoning is billed from the output allowance.** ``max_output_tokens`` caps
thinking plus response text together, so a budget sized for the expected JSON
gets eaten by reasoning and the answer arrives truncated as
``finish_reason: MAX_TOKENS`` with empty or partial text (research.md R14).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from docdoc.extraction.adapter import (
    Availability,
    ExtractionOptions,
    ModelResponse,
    ModelUsage,
)
from docdoc.extraction.errors import ExtractionError, ModelProviderError

if TYPE_CHECKING:
    from docdoc.extraction.prompt import ModelRequest

__all__ = ["ADAPTER_ID", "DEFAULT_MODEL", "GeminiAdapter"]

ADAPTER_ID = "gemini"

#: Bumped when this adapter's output changes for unchanged inputs. It is embedded
#: in ``extractor_version`` and therefore reaches the artifact id (FR-036).
ADAPTER_VERSION = "1.0.0"

#: Verified against the live API at T053/T059 rather than written from memory --
#: the first value here was ``gemini-2.5-pro``, which `models.list()` still
#: reports and which the API refuses to new accounts with a 404. Listing a model
#: is not the same as being able to call it, and only a call tells you which.
#:
#: A deliberate non-choice: **not** an alias like ``gemini-pro-latest``. An alias
#: makes results irreproducible, because the model moves underneath a recorded
#: request -- the same objection FR-014 makes to resolving ``latest`` for schemas.
#: Provenance records the concrete version the provider reports (see
#: ``model_version`` below), so an alias would also record a name that means
#: something different next month.
#:
#: The tier is still provisional: T059 measures accuracy, cost, and latency across
#: tiers, and a pro tier was unreachable on the account used here (429).
DEFAULT_MODEL = "gemini-3.5-flash"

#: Finish reasons that mean "the model declined", mapped to the category recorded
#: verbatim in the error. They are *not* interchangeable:
#:
#: ``RECITATION`` is not misconduct. It fires when output resembles copyrighted
#: material, and an invoice quoting standard payment terms can trip it. Reporting
#: it as a safety refusal sends the caller after the wrong problem.
#:
#: ``SPII`` -- sensitive personally identifiable information -- matters more here
#: than in most applications. This engine's whole job is documents full of names,
#: addresses, and account numbers, so this is a reason to expect rather than an
#: edge case.
_REFUSALS = {
    "SAFETY": "safety",
    "PROHIBITED_CONTENT": "prohibited_content",
    "BLOCKLIST": "blocklist",
    "RECITATION": "recitation",
    "SPII": "sensitive_personal_information",
    "LANGUAGE": "unsupported_language",
    "IMAGE_SAFETY": "image_safety",
}

#: HTTP status codes worth another attempt. Everything else fails on the first
#: try: re-sending a malformed request or a rejected credential gets the same
#: answer, and retrying it only spends the deadline (FR-025).
_TRANSIENT_CODES = {
    408: "timeout",
    429: "rate_limit",
    500: "service",
    502: "service",
    503: "service",
    504: "timeout",
}

_PERMANENT_CODES = {
    400: "request",
    401: "auth",
    403: "auth",
    404: "request",
    413: "request",
}


class GeminiAdapter:
    """Answers a structured request by calling Gemini.

    The client is constructed lazily, so importing this module -- which
    ``available()`` needs to do -- costs nothing and needs no credential.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client = client

    # -- the contract -------------------------------------------------------

    @property
    def id(self) -> str:
        return ADAPTER_ID

    @property
    def version(self) -> str:
        """Embeds the SDK version, the way ingest's ``parser_version`` does.

        A library upgrade that changes output must change identity, and this is
        where that happens for the extract stage.
        """
        return f"{ADAPTER_VERSION}+google-genai-{_sdk_version()}"

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def model_version(self) -> str:
        """The model *requested*, used only when a response has not been seen.

        The response carries its own ``model_version``, and that is what reaches
        provenance -- FR-033 wants the model actually reached, not the one asked
        for. The two differ whenever a request names an alias, and recording the
        request would then record a name whose meaning moves.
        """
        return self._model

    def available(self) -> Availability:
        """Usable, or unusable with the reason named (FR-028).

        Both failure modes are reported rather than raised, because the caller
        needs to distinguish "not installed" from "no such thing" and silence
        makes them identical.
        """
        try:
            import google.genai  # noqa: F401
        except ImportError:
            return Availability(
                usable=False,
                reason="the google-genai SDK is not installed; install docdoc[google]",
            )
        if self._client is None and not self._resolved_key():
            return Availability(
                usable=False,
                reason="no API key configured; set GEMINI_API_KEY or GOOGLE_API_KEY",
            )
        return Availability(usable=True)

    def complete(self, request: ModelRequest, options: ExtractionOptions) -> ModelResponse:
        """One structured answer, or a typed docdoc error. Never a partial one."""
        from google.genai import errors as genai_errors

        client = self._ensure_client(request)
        config = self._config(request, options)

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=request.document_text,
                config=config,
            )
        except genai_errors.APIError as exc:
            raise self._translate(exc, request) from exc

        return self._read(response, request, options)

    # -- request construction -----------------------------------------------

    def _config(self, request: ModelRequest, options: ExtractionOptions) -> Any:
        from google.genai import types

        thinking = None
        if options.thinking_budget is not None:
            # `include_thoughts` stays off deliberately. The reasoning is not
            # needed, and storing it would put model prose into a result whose
            # every other field is a document value or an identifier.
            thinking = types.ThinkingConfig(
                thinking_budget=options.thinking_budget, include_thoughts=False
            )

        return types.GenerateContentConfig(
            # The per-schema prefix goes here rather than into `contents`, so it
            # precedes the document and stays byte-identical across every
            # document extracted against one schema (research.md R15).
            system_instruction=request.prefix,
            response_mime_type="application/json",
            # Standard JSON Schema, which is what the projection produces.
            # `response_schema` would want the SDK's own OpenAPI-subset type.
            response_json_schema=request.response_shape,
            temperature=options.temperature,
            top_p=options.top_p,
            top_k=options.top_k,
            seed=options.seed,
            max_output_tokens=options.max_output_tokens,
            thinking_config=thinking,
        )

    # -- response reading ---------------------------------------------------

    def _read(
        self, response: Any, request: ModelRequest, options: ExtractionOptions
    ) -> ModelResponse:
        """Branch on the outcome *before* reading any content."""
        blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
        if blocked is not None:
            # The prompt never reached generation, so there is no candidate at
            # all. A distinct condition from an output block, and worth saying so.
            raise ModelProviderError(
                f"the prompt was blocked before generation ({_name(blocked)})",
                reason="refusal",
                adapter_id=ADAPTER_ID,
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                refusal_category=f"prompt_blocked:{_name(blocked).lower()}",
                attempts=1,
            )

        candidates = getattr(response, "candidates", None) or ()
        if not candidates:
            raise ModelProviderError(
                "the provider returned no candidate and no block reason",
                reason="service",
                adapter_id=ADAPTER_ID,
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                attempts=1,
            )

        finish = _name(getattr(candidates[0], "finish_reason", None))
        usage = _usage(response)

        if finish in _REFUSALS:
            raise ModelProviderError(
                f"the model declined to answer ({finish})",
                reason="refusal",
                adapter_id=ADAPTER_ID,
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                refusal_category=_REFUSALS[finish],
                attempts=1,
            )

        if finish == "MAX_TOKENS":
            produced = (usage.output_tokens or 0) + (usage.reasoning_tokens or 0)
            raise ExtractionError(
                f"the response was truncated at the {options.max_output_tokens:,}-token output "
                f"budget, having produced {produced:,} tokens "
                f"({usage.output_tokens or 0:,} of answer and {usage.reasoning_tokens or 0:,} of "
                "reasoning), so it cannot be the requested shape. Reasoning is billed from the "
                "same allowance, so raise max_output_tokens or lower thinking_budget",
                reason="truncated",
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                adapter_id=ADAPTER_ID,
            )

        if finish not in ("STOP", "FINISH_REASON_UNSPECIFIED", ""):
            raise ModelProviderError(
                f"the provider stopped for an unhandled reason ({finish})",
                reason="service",
                adapter_id=ADAPTER_ID,
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                attempts=1,
            )

        return ModelResponse(
            payload=self._payload(response, request),
            model_id=self._model,
            # What the provider says it used, falling back to what we asked for.
            # These differ for an alias, and the resolved one is the answerable
            # record (FR-033).
            model_version=_name(getattr(response, "model_version", None)) or self._model,
            usage=usage,
        )

    def _payload(self, response: Any, request: ModelRequest) -> dict[str, Any]:
        text = getattr(response, "text", None)
        if not text:
            raise ExtractionError(
                "the provider returned an empty body for a successful stop reason",
                reason="shape",
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                adapter_id=ADAPTER_ID,
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # The response format was enforced server-side, so this should be
            # unreachable. It is checked anyway: "the provider promised" and "the
            # bytes that arrived" are different claims.
            raise ExtractionError(
                f"the provider's response was not valid JSON despite an enforced "
                f"response format: {exc.msg}",
                reason="shape",
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                adapter_id=ADAPTER_ID,
            ) from exc
        if not isinstance(payload, dict):
            raise ExtractionError(
                f"expected a JSON object at the response root, got {type(payload).__name__}",
                reason="shape",
                schema_identity=request.schema_identity,
                document_id=request.document_id,
                adapter_id=ADAPTER_ID,
            )
        return payload

    # -- error translation --------------------------------------------------

    def _translate(self, exc: Any, request: ModelRequest) -> ModelProviderError:
        """Every provider exception becomes a docdoc error (FR-042).

        ``__cause__`` is preserved by the ``raise ... from`` at the call site, so
        the provider's own traceback survives for debugging without its type
        crossing the boundary.
        """
        code = getattr(exc, "code", None)
        reason: str | None = None
        if isinstance(code, int):
            reason = _TRANSIENT_CODES.get(code) or _PERMANENT_CODES.get(code)
        if reason is None:
            # An unmapped code is treated as a service failure, which is
            # retryable. That is the safe direction: a transient failure
            # misclassified as permanent fails a job that would have succeeded,
            # while the reverse costs at most the configured attempt limit.
            reason = "service"
        return ModelProviderError(
            f"the provider rejected the request ({code}): {getattr(exc, 'message', exc)}",
            reason=reason,
            adapter_id=ADAPTER_ID,
            schema_identity=request.schema_identity,
            document_id=request.document_id,
            attempts=1,
        )

    # -- client -------------------------------------------------------------

    def _resolved_key(self) -> str | None:
        import os

        return self._api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def _ensure_client(self, request: ModelRequest) -> Any:
        if self._client is not None:
            return self._client
        availability = self.available()
        if not availability.usable:
            raise ModelProviderError(
                f"adapter {ADAPTER_ID!r} is unavailable: {availability.reason}",
                reason="unavailable",
                adapter_id=ADAPTER_ID,
                schema_identity=request.schema_identity,
                document_id=request.document_id,
            )
        from google import genai

        self._client = genai.Client(api_key=self._resolved_key())
        return self._client


def _name(value: Any) -> str:
    """The name of an SDK enum member, or its string form.

    Recorded rather than compared by identity so a fixture can carry a plain
    string and a live response can carry the enum, without the reading code
    caring which it got.
    """
    if value is None:
        return ""
    return str(getattr(value, "name", value))


def _usage(response: Any) -> ModelUsage:
    """Token counts, mapped from the provider's names to docdoc's.

    ``cached_content_token_count`` is the cache-hit field -- not the
    ``total_cached_tokens`` the public docs page names, which is why this is read
    from the SDK's own model rather than from prose.
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return ModelUsage()
    return ModelUsage(
        input_tokens=getattr(meta, "prompt_token_count", None),
        output_tokens=getattr(meta, "candidates_token_count", None),
        cache_read_input_tokens=getattr(meta, "cached_content_token_count", None),
        reasoning_tokens=getattr(meta, "thoughts_token_count", None),
    )


def _sdk_version() -> str:
    try:
        from importlib.metadata import version

        return version("google-genai")
    except Exception:  # pragma: no cover - a missing SDK is reported by available()
        return "unknown"
