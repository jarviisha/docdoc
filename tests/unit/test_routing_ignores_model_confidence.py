"""T152, SC-017 — the negative test that **is** the requirement.

Principle II keeps a model's self-reported confidence visible and inert, and
ADR-0004 records why: it is a number the model chose, about its own output, with
no external referent. A deployment routing on it would find its review rate move
when a vendor shipped a new checkpoint, with no change to any document, any
schema, or any policy.

"Routing does not read `model_confidence`" is a claim about what does **not**
happen, and a claim of that shape has no form other than a test. So: vary that
field alone, across a fixture set, and require the decision not to move. Not the
outcome alone — the whole decision, reasons and version included, because a
confidence that changed which *reason* was reported would be a confidence that
reached the decision.
"""

from __future__ import annotations

import pytest

from docdoc.grounding.result import GroundingOutcome, GroundingStatus
from docdoc.runs.routing import RoutingOutcome, RoutingPolicy, decide
from docdoc.validation.result import Verdict

CONFIDENCES = (None, 0.0, 0.01, 0.5, 0.94, 0.999, 1.0)

POLICY = RoutingPolicy(
    version="test@1",
    required_fields=frozenset({"total", "invoice_number"}),
    review_when_required_fuzzy_below=0.9,
)


def _grounding(**statuses: tuple[str, float | None]):
    """A grounding result, as much of one as `decide` reads.

    `GroundingOutcome` is the real model, because the `status` and `score` it
    validates are exactly the two fields routing reads — a stand-in for *that*
    would be testing the stand-in. The container is not, because
    `GroundingResult` additionally requires provenance, counts, a view id, and an
    options hash, none of which routing looks at, and a fixture that built them
    would be a fixture about grounding.
    """
    return _Grounding(
        {
            field: GroundingOutcome(field_path=field, status=GroundingStatus(status), score=score)
            for field, (status, score) in statuses.items()
        }
    )


class _Grounding:
    def __init__(self, outcomes: dict) -> None:
        self.outcomes = outcomes


class _Validation:
    """The verdict and the findings, which is what routing reads."""

    def __init__(self, verdict: Verdict, findings: tuple = ()) -> None:
        self.verdict = verdict
        self.findings = findings


class _Result:
    """A stand-in for `PipelineResult` carrying a `model_confidence` that varies.

    A real `PipelineResult` would need an `ExtractionResult` per case, and the
    fixture would then be about building extractions rather than about the one
    field under test. What `decide` reads is `grounding` and `validation`; what
    this varies is a field beside them. If `decide` ever reaches for the
    extraction, this attribute is what it will find, and the assertions below are
    what will fail.
    """

    def __init__(self, *, grounding, validation, model_confidence) -> None:
        self.grounding = grounding
        self.validation = validation
        self.model_confidence = model_confidence
        self.extraction = _Extraction(model_confidence)
        self.processing_id = "sha256:" + "d" * 64
        self.outcomes = ()


class _Extraction:
    """Where a real result carries confidence: on the extracted values."""

    def __init__(self, confidence: float | None) -> None:
        self.model_confidence = confidence
        self.values = {"total": _Value(confidence), "invoice_number": _Value(confidence)}


class _Value:
    def __init__(self, confidence: float | None) -> None:
        self.model_confidence = confidence


#: Four fixtures spanning both outcomes and both signals, so the invariance is
#: asserted where the decision is `automatic`, where it is `review` for a
#: validation reason, and where it is `review` for a grounding reason. A test
#: that only ever saw one outcome could pass against an implementation that
#: always returned it.
FIXTURES = {
    "clean": (
        _grounding(total=("exact", 1.0), invoice_number=("exact", 1.0)),
        _Validation(Verdict.VALID),
    ),
    "invalid_verdict": (
        _grounding(total=("exact", 1.0), invoice_number=("exact", 1.0)),
        _Validation(Verdict.INVALID),
    ),
    "ungrounded_required": (
        _grounding(total=("ungrounded", None), invoice_number=("exact", 1.0)),
        _Validation(Verdict.VALID),
    ),
    "weak_fuzzy": (
        _grounding(total=("fuzzy", 0.4), invoice_number=("exact", 1.0)),
        _Validation(Verdict.VALID),
    ),
    "strong_fuzzy": (
        _grounding(total=("fuzzy", 0.98), invoice_number=("exact", 1.0)),
        _Validation(Verdict.VALID),
    ),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_the_decision_does_not_move_when_confidence_alone_moves(name: str) -> None:
    """**The requirement.** One fixture, seven confidences, one decision."""
    grounding, validation = FIXTURES[name]

    decisions = [
        decide(
            _Result(grounding=grounding, validation=validation, model_confidence=c),  # type: ignore[arg-type]
            POLICY,
        )
        for c in CONFIDENCES
    ]

    first = decisions[0]
    for confidence, decision in zip(CONFIDENCES, decisions, strict=True):
        assert decision == first, (
            f"the routing decision for fixture {name!r} moved when "
            f"model_confidence became {confidence!r}. Principle II keeps that "
            f"number inert, and ADR-0004 records why: it is a number the model "
            f"chose about its own output, with no external referent"
        )


def test_the_fixtures_do_not_all_decide_the_same_way() -> None:
    """Guards the guard.

    An implementation that returned `automatic` unconditionally would satisfy
    every assertion above. The fixtures have to disagree with each other for the
    invariance to mean anything.
    """
    outcomes = {
        name: decide(
            _Result(grounding=g, validation=v, model_confidence=0.5),  # type: ignore[arg-type]
            POLICY,
        ).outcome
        for name, (g, v) in FIXTURES.items()
    }

    assert outcomes["clean"] is RoutingOutcome.AUTOMATIC
    assert outcomes["invalid_verdict"] is RoutingOutcome.REVIEW
    assert outcomes["ungrounded_required"] is RoutingOutcome.REVIEW
    assert outcomes["weak_fuzzy"] is RoutingOutcome.REVIEW
    assert outcomes["strong_fuzzy"] is RoutingOutcome.AUTOMATIC


def test_the_module_names_model_confidence_nowhere_outside_its_own_argument() -> None:
    """The structural half, because the behavioural one cannot see a field that
    is read and then discarded."""
    import inspect

    from docdoc.runs import routing

    source = inspect.getsource(routing.decide)

    assert "model_confidence" not in source
    assert ".extraction" not in source, (
        "`decide` reached for the extraction result, which is where "
        "`model_confidence` lives (ADR-0004)"
    )
