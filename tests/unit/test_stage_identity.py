"""What each stage folds, asserted input by input (FR-058).

This is the test the whole reuse mechanism rests on, and the reason it exists is
that its subject is invisible everywhere else. Fold too little and the store
returns a result computed under inputs that have since changed — the stale-cache
bug ADR-0003 was written to close. Fold too much and reuse never happens, so a
cache that cost real work quietly does nothing. **Neither shows up in a result.**
A wrong answer from a stale cache looks exactly like a right one.

So each stage's options hash is exercised by moving one input at a time and
asserting the hash moves with it, and by moving something that cannot change a
result and asserting it does not.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from docdoc.extraction.identity import options_hash_for_extraction
from docdoc.grounding.identity import options_hash_for_grounding
from docdoc.grounding.options import GroundingOptions
from docdoc.kernel import document_id_for
from docdoc.pipeline import PIPELINE_ID, PIPELINE_VERSION, STAGE_SPECS, Stage
from docdoc.validation.identity import options_hash_for_validation
from docdoc.validation.options import ValidationOptions

# -- the list of four -------------------------------------------------------


def test_there_are_exactly_four_stages_and_they_are_in_order() -> None:
    """Not a tautology: the pipeline must not become a DAG engine (Principle XI)."""
    assert [stage.value for stage in Stage] == ["parse", "extract", "ground", "validate"]


def test_every_stage_has_a_spec_with_a_format_version() -> None:
    assert set(STAGE_SPECS) == set(Stage)
    for stage, spec in STAGE_SPECS.items():
        assert spec.stage is stage
        assert spec.processor_id != ""
        assert spec.artifact_format_version >= 1


def test_the_pipeline_is_itself_a_versioned_processor() -> None:
    """ADR-0003 folds `pipeline_version` into the terminal artifact."""
    assert PIPELINE_ID != ""
    assert PIPELINE_VERSION != ""


# -- parse ------------------------------------------------------------------

_PARSE = {
    "blob_id": "sha256:" + "a" * 64,
    "parser_id": "pdf-text",
    "parser_version": "1.0.0",
    "options_hash": "sha256:" + "b" * 64,
}


@pytest.mark.parametrize("field", sorted(_PARSE))
def test_every_parse_input_moves_the_document_id(field: str) -> None:
    base = document_id_for(**_PARSE)  # type: ignore[arg-type]
    moved = document_id_for(**{**_PARSE, field: _PARSE[field] + "x"})  # type: ignore[arg-type]
    assert base != moved, f"{field} does not reach document_id"


# -- extract ----------------------------------------------------------------

_EXTRACT: dict[str, object] = {
    "schema_identity": "invoice@1",
    "schema_hash": "sha256:" + "c" * 64,
    "prompt_hash": "sha256:" + "d" * 64,
    "projection_id": "flat@1",
    "model_id": "gemini-3.5-flash",
    "model_version": "001",
    "max_output_tokens": 2048,
    "temperature": 0.0,
    "top_p": None,
    "top_k": None,
    "seed": None,
    "thinking_budget": None,
    "input_budget_tokens": 100_000,
}

_MOVED: dict[str, object] = {
    "schema_identity": "invoice@2",
    "schema_hash": "sha256:" + "e" * 64,
    "prompt_hash": "sha256:" + "f" * 64,
    "projection_id": "flat@2",
    "model_id": "gemini-3.5-pro",
    "model_version": "002",
    "max_output_tokens": 4096,
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "seed": 7,
    "thinking_budget": 1024,
    "input_budget_tokens": 50_000,
}


def test_the_extract_row_folds_every_input_adr_0003_names() -> None:
    """ADR-0003's Extract row, checked against the code rather than the prose."""
    required = {
        "schema_identity",
        "schema_hash",
        "prompt_hash",
        "model_id",
        "model_version",
        "temperature",
        "top_p",
        "seed",
        "max_output_tokens",
    }
    assert required <= set(_EXTRACT), "the ADR names an input this test does not cover"


@pytest.mark.parametrize("field", sorted(_EXTRACT))
def test_every_extract_input_moves_the_options_hash(field: str) -> None:
    base = options_hash_for_extraction(**_EXTRACT)  # type: ignore[arg-type]
    moved = options_hash_for_extraction(**{**_EXTRACT, field: _MOVED[field]})  # type: ignore[arg-type]
    assert base != moved, f"{field} can change an extraction and does not reach its identity"


# -- ground -----------------------------------------------------------------


def test_grounding_options_reach_the_hash() -> None:
    base = options_hash_for_grounding(GroundingOptions())
    assert base != options_hash_for_grounding(GroundingOptions(threshold=0.5))
    assert base != options_hash_for_grounding(GroundingOptions(candidate_budget=3))


# -- validate ---------------------------------------------------------------


def test_the_enabled_rule_set_reaches_the_validation_hash() -> None:
    """Which rules ran is not in `schema_hash`; only this says it."""
    options = ValidationOptions()
    one = options_hash_for_validation(options, enabled_rules=("a",))
    two = options_hash_for_validation(options, enabled_rules=("a", "b"))
    assert one != two


def test_the_enabled_rule_set_is_order_insensitive() -> None:
    options = ValidationOptions()
    forward = options_hash_for_validation(options, enabled_rules=("a", "b"))
    backward = options_hash_for_validation(options, enabled_rules=("b", "a"))
    assert forward == backward


def test_the_grounding_policy_reaches_the_validation_hash() -> None:
    """A deployment that raises ungrounded from warning to error changed what its
    verdicts mean, and its artifact ids must move with it (ADR-0003 amendment)."""
    from docdoc.validation.options import GroundingPolicy
    from docdoc.validation.severity import Severity

    lenient = ValidationOptions()
    strict = ValidationOptions(grounding_policy=GroundingPolicy(ungrounded=Severity.ERROR))
    relaxed = options_hash_for_validation(lenient, enabled_rules=())
    tightened = options_hash_for_validation(strict, enabled_rules=())
    assert relaxed != tightened


# -- what must NOT reach an identity (FR-060) -------------------------------


def test_a_duration_cannot_be_folded_into_an_identity() -> None:
    """Not a mechanism test — a shape test.

    None of the four options-hash functions accepts a duration, a timestamp, a
    request id, or a retry count, and none can be passed one. An identity that
    moved with how busy the machine was would make every cache key useless, and
    ingest already separates `ParseOptions` from `TransportSettings` for exactly
    this reason.
    """
    import inspect

    forbidden = {"duration", "duration_ms", "started_at", "timestamp", "request_id",
                 "attempt", "attempts", "retries", "timeout", "deadline", "elapsed"}
    for function in (
        options_hash_for_extraction,
        options_hash_for_grounding,
        options_hash_for_validation,
        document_id_for,
    ):
        names = set(inspect.signature(function).parameters)
        assert not (names & forbidden), f"{function.__name__} accepts a non-identity input"


def test_decimal_values_are_not_silently_accepted_as_floats() -> None:
    """Guards the canonical encoder's contract, which every hash here depends on."""
    with pytest.raises(Exception, match=r"encode|JSON|serial"):
        options_hash_for_extraction(**{**_EXTRACT, "temperature": Decimal("0.1")})  # type: ignore[arg-type]
