"""T048, T058, T060a — the Gemini adapter, against recorded scrubbed responses.

This is the code that produces every real extraction. Without these fixtures it
would be exercised only where credentials exist, so CI would never test it — the
argument Milestone 2's Complexity Tracking made for the same reason.

The fixtures are hand-written from the SDK's own response model, reduced to the
fields the adapter reads. No credentials, no real document, no account numbers.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from docdoc.extraction import ExtractionError, ModelProviderError, response_shape_for
from docdoc.extraction.adapters.gemini import ADAPTER_ID, DEFAULT_MODEL, GeminiAdapter
from docdoc.extraction.prompt import build_request
from docdoc.extraction.registry import SchemaRegistry

FIXTURES = pathlib.Path("tests/fixtures/gemini")


# -- a fake client that replays a recorded response ---------------------------


class _FakeCandidate:
    def __init__(self, finish_reason: str | None) -> None:
        self.finish_reason = finish_reason


class _FakeFeedback:
    def __init__(self, block_reason: str | None) -> None:
        self.block_reason = block_reason


class _FakeUsage:
    def __init__(self, values: dict[str, int]) -> None:
        for key in (
            "prompt_token_count",
            "candidates_token_count",
            "cached_content_token_count",
            "thoughts_token_count",
        ):
            setattr(self, key, values.get(key))


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.text = body.get("text")
        blocked = body.get("block_reason")
        self.candidates = () if blocked else (_FakeCandidate(body.get("finish_reason")),)
        self.prompt_feedback = _FakeFeedback(body.get("block_reason"))
        self.usage_metadata = _FakeUsage(body.get("usage", {}))


class _FakeModels:
    def __init__(self, body: dict[str, Any] | Exception) -> None:
        self._body = body
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: Any, config: Any) -> _FakeResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self._body, Exception):
            raise self._body
        return _FakeResponse(self._body)


class _FakeClient:
    def __init__(self, body: dict[str, Any] | Exception) -> None:
        self.models = _FakeModels(body)


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


def _request(registry: SchemaRegistry, identity: str = "invoice@1"):
    entry = registry.resolve(identity)
    return build_request(
        entry, "a scrubbed document body", response_shape=response_shape_for(entry.schema)
    )


def _adapter(fixture: str | Exception) -> tuple[GeminiAdapter, _FakeClient]:
    body = fixture if isinstance(fixture, Exception) else _load(fixture)
    client = _FakeClient(body)
    return GeminiAdapter(client=client), client


# -- identity and availability -----------------------------------------------


def test_the_adapter_reports_its_identity_and_embeds_the_sdk_version() -> None:
    """FR-036 — a library upgrade that changes output must change identity."""
    adapter = GeminiAdapter()
    assert adapter.id == ADAPTER_ID == "gemini"
    assert adapter.version.startswith("1.0.0+google-genai-")
    assert adapter.model_id == DEFAULT_MODEL


def test_a_missing_key_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-028 — with the reason, so 'not installed' stays distinct from 'no such thing'."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    availability = GeminiAdapter().available()
    assert availability.usable is False
    assert "no API key configured" in (availability.reason or "")


def test_an_explicit_key_makes_it_available() -> None:
    assert GeminiAdapter(api_key="test-key").available().usable is True


def test_an_unavailable_adapter_raises_before_any_call(
    registry: SchemaRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from docdoc.extraction import ExtractionOptions

    with pytest.raises(ModelProviderError) as caught:
        GeminiAdapter().complete(_request(registry), ExtractionOptions())
    assert caught.value.reason == "unavailable"


# -- the request -------------------------------------------------------------


def test_every_folded_parameter_reaches_the_request(registry: SchemaRegistry) -> None:
    """T058 — the inverse of what this test was first written to assert.

    All of these exist on this provider (research.md R4), so the failure mode is
    not "a rejected parameter" but "a parameter folded into the artifact id that
    the call never sent" — which would make the identity claim something the
    request did not do.
    """
    from docdoc.extraction import ExtractionOptions

    adapter, client = _adapter("ok")
    options = ExtractionOptions(
        max_output_tokens=4096, temperature=0.2, top_p=0.9, top_k=40, seed=17, thinking_budget=512
    )
    adapter.complete(_request(registry), options)

    config = client.models.calls[0]["config"]
    assert config.temperature == 0.2
    assert config.top_p == 0.9
    assert config.top_k == 40
    assert config.seed == 17
    assert config.max_output_tokens == 4096
    assert config.thinking_config.thinking_budget == 512


def test_reasoning_summaries_are_never_requested(registry: SchemaRegistry) -> None:
    """Storing model prose in a result of document values is not what this is for."""
    from docdoc.extraction import ExtractionOptions

    adapter, client = _adapter("ok")
    adapter.complete(_request(registry), ExtractionOptions(thinking_budget=256))
    assert client.models.calls[0]["config"].thinking_config.include_thoughts is False


def test_the_schema_goes_in_response_json_schema(registry: SchemaRegistry) -> None:
    """The projection produces JSON Schema; `response_schema` wants another dialect."""
    from docdoc.extraction import ExtractionOptions

    adapter, client = _adapter("ok")
    adapter.complete(_request(registry), ExtractionOptions())
    config = client.models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema is not None
    assert config.response_schema is None
    assert "total" in config.response_json_schema["properties"]


def test_the_cached_prefix_is_the_system_instruction_and_the_document_is_not(
    registry: SchemaRegistry,
) -> None:
    """R15 — the per-schema prefix must precede the volatile part."""
    from docdoc.extraction import ExtractionOptions

    adapter, client = _adapter("ok")
    request = _request(registry)
    adapter.complete(request, ExtractionOptions())
    call = client.models.calls[0]
    assert call["config"].system_instruction == request.prefix
    assert "scrubbed document body" in call["contents"]
    assert "scrubbed document body" not in call["config"].system_instruction


def test_the_configured_model_is_the_one_called(registry: SchemaRegistry) -> None:
    from docdoc.extraction import ExtractionOptions

    adapter = GeminiAdapter(model="gemini-2.5-flash", client=_FakeClient(_load("ok")))
    response = adapter.complete(_request(registry), ExtractionOptions())
    assert response.model_id == "gemini-2.5-flash"


# -- the successful response -------------------------------------------------


def test_a_successful_response_is_mapped_and_conforms(registry: SchemaRegistry) -> None:
    from docdoc.extraction import ExtractionOptions
    from docdoc.extraction.conform import conform

    adapter, _ = _adapter("ok")
    response = adapter.complete(_request(registry), ExtractionOptions())
    report = conform(response.payload, registry.resolve("invoice@1").schema, adapter_id="gemini")
    assert report.discarded == ()
    assert report.values["total"].claimed_text == "2.480,50", "byte-faithful, comma decimal intact"
    assert len(report.values["line_items"]) == 2


def test_usage_is_mapped_from_the_sdk_field_names(registry: SchemaRegistry) -> None:
    """`cached_content_token_count`, not the `total_cached_tokens` the docs page names.

    Read from the SDK's own model rather than from prose, which is why this
    assertion exists.
    """
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("ok")
    usage = adapter.complete(_request(registry), ExtractionOptions()).usage
    assert usage.input_tokens == 4210
    assert usage.output_tokens == 612
    assert usage.cache_read_input_tokens == 0, "R15: no cache hit yet, the prefix is too short"
    assert usage.reasoning_tokens == 1840, "reasoning is billed from the output allowance (R14)"


# -- refusals: T060a ---------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "category"),
    [
        ("refusal_safety", "safety"),
        ("refusal_recitation", "recitation"),
        ("refusal_spii", "sensitive_personal_information"),
        ("refusal_blocklist", "blocklist"),
    ],
)
def test_each_refusal_branch_is_permanent_and_keeps_its_category(
    registry: SchemaRegistry, fixture: str, category: str
) -> None:
    """R12 — four categories that do not mean the same thing.

    Collapsing them would lose the distinction between "we will not answer this"
    and "the answer looked like a quotation", and both are permanent for different
    reasons.
    """
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter(fixture)
    with pytest.raises(ModelProviderError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.reason == "refusal"
    assert caught.value.transient is False
    assert caught.value.refusal_category == category
    assert caught.value.adapter_id == "gemini"


def test_recitation_is_not_reported_as_a_safety_refusal(registry: SchemaRegistry) -> None:
    """It fires on resemblance to copyrighted text.

    An invoice quoting standard payment terms can trip it. Calling that a safety
    refusal sends the reader looking for misconduct that is not there.
    """
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("refusal_recitation")
    with pytest.raises(ModelProviderError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.refusal_category == "recitation"
    assert "safety" not in caught.value.refusal_category


def test_spii_is_its_own_category_because_this_engine_reads_documents_full_of_it(
    registry: SchemaRegistry,
) -> None:
    """A reason to expect rather than an edge case, for an IDP engine."""
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("refusal_spii")
    with pytest.raises(ModelProviderError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.refusal_category == "sensitive_personal_information"


def test_a_blocked_prompt_is_distinct_from_a_blocked_output(registry: SchemaRegistry) -> None:
    """No candidate exists at all, which is a different diagnosis."""
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("prompt_blocked")
    with pytest.raises(ModelProviderError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.refusal_category == "prompt_blocked:prohibited_content"
    assert "before generation" in str(caught.value)


# -- truncation and malformed bodies -----------------------------------------


def test_truncation_is_an_extraction_error_not_a_retry(registry: SchemaRegistry) -> None:
    """R14 — and the message names the cause, because the cause is unobvious.

    Reasoning is billed from `max_output_tokens`, so a budget that looks generous
    for the JSON gets consumed before the answer starts.
    """
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("truncated")
    with pytest.raises(ExtractionError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.reason == "truncated"
    assert "Reasoning is billed from the same allowance" in str(caught.value)


def test_an_empty_body_with_a_successful_stop_reason_is_an_error(
    registry: SchemaRegistry,
) -> None:
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("empty_body")
    with pytest.raises(ExtractionError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.reason == "shape"


def test_prose_where_json_was_enforced_is_an_error(registry: SchemaRegistry) -> None:
    """Should be unreachable — the format is enforced server-side. Checked anyway.

    "The provider promised" and "the bytes that arrived" are different claims.
    """
    from docdoc.extraction import ExtractionOptions

    adapter, _ = _adapter("not_json")
    with pytest.raises(ExtractionError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.reason == "shape"
    assert "despite an enforced response format" in str(caught.value)


# -- error translation -------------------------------------------------------


class _StubAPIError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"stub {code}")
        self.code = code
        self.message = f"stub {code}"


@pytest.mark.parametrize(
    ("code", "reason", "transient"),
    [
        (408, "timeout", True),
        (429, "rate_limit", True),
        (500, "service", True),
        (503, "service", True),
        (400, "request", False),
        (401, "auth", False),
        (403, "auth", False),
        (404, "request", False),
        (413, "request", False),
        (599, "service", True),
    ],
)
def test_status_codes_map_to_the_right_class(
    registry: SchemaRegistry,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    reason: str,
    transient: bool,
) -> None:
    """FR-025 — and an unmapped code is treated as transient, which is the safe way to be wrong.

    A transient failure misclassified as permanent fails a job that would have
    succeeded; the reverse costs at most the configured attempt limit.
    """
    from google.genai import errors as genai_errors

    from docdoc.extraction import ExtractionOptions

    monkeypatch.setattr(genai_errors, "APIError", _StubAPIError, raising=False)
    adapter, _ = _adapter(_StubAPIError(code))
    with pytest.raises(ModelProviderError) as caught:
        adapter.complete(_request(registry), ExtractionOptions())
    assert caught.value.reason == reason
    assert caught.value.transient is transient
    assert isinstance(caught.value.__cause__, _StubAPIError), "__cause__ preserved (FR-042)"


def test_no_provider_exception_type_escapes(registry: SchemaRegistry, monkeypatch) -> None:
    """FR-042 — the untyped provider world stops at this module."""
    from google.genai import errors as genai_errors

    from docdoc.extraction import ExtractionOptions

    monkeypatch.setattr(genai_errors, "APIError", _StubAPIError, raising=False)
    adapter, _ = _adapter(_StubAPIError(500))
    with pytest.raises(ModelProviderError):
        adapter.complete(_request(registry), ExtractionOptions())
