"""T031 — location agreement is three-valued and a separate axis (FR-038, EVA-14, SC-002).

Two things are being asserted, and the second matters more than the first.

**Three values, not two.** ``NOT_ASSESSABLE`` exists because
``GroundingOutcome.geometry is None`` means *the parser supplied no geometry*,
while ``()`` means geometry exists and covers no tokens. Milestone 4's GRD-17
refuses to collapse those, and collapsing the first into ``DISAGREES`` here would
report a parser's silence as a grounding error -- making the mislocation rate a
function of which parser ran rather than of where values were found. A team that
switched to a geometry-less parser would watch their mislocation rate climb while
nothing about their extraction changed.

**A separate axis from the field outcome, never a seventh member of it.** A value
can be correct and mislocated, or wrong and perfectly located. Merging the two
would hide the failure this rule exists to catch: a plausible wrong span that
resolves cleanly and points at the wrong text.

The rule is **containment, not IoU**, and the reason is the pairing it is applied
to: a human labelling a value draws a loose box, and docdoc's geometry is tight on
the tokens. IoU punishes exactly that combination -- a perfectly located tight box
inside a generous hand-drawn one scores well below 0.5 IoU while being completely
right.
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import ExpectedLocation, FieldOutcomeKind, LocationAgreement, evaluate
from docdoc.evaluation.location import CONTAINMENT_THRESHOLD, agreement_for
from docdoc.grounding.result import GroundingOutcome, GroundingStatus
from docdoc.kernel import BBox, Geometry, Span
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

PAGE = 0
LABEL_BOX = BBox(0.1, 0.1, 0.5, 0.2)


def _outcome(
    *,
    status: GroundingStatus = GroundingStatus.EXACT,
    pages: tuple[int, ...] = (PAGE,),
    geometry: tuple[Geometry, ...] | None = (),
) -> GroundingOutcome:
    return GroundingOutcome(
        field_path="total",
        status=status,
        score=1.0 if status is not GroundingStatus.UNGROUNDED else None,
        span=None if status is GroundingStatus.UNGROUNDED else Span(0, 5),
        pages=pages,
        geometry=geometry,
    )


def _geometry(box: BBox, page: int = PAGE) -> tuple[Geometry, ...]:
    return (Geometry(page_index=page, bbox=box),)


# -- AGREES ------------------------------------------------------------------


def test_a_correctly_located_value_agrees() -> None:
    """Tight geometry inside the label's box: the ordinary success case."""
    agreement = agreement_for(
        ExpectedLocation(page=PAGE, bbox=LABEL_BOX),
        _outcome(geometry=_geometry(BBox(0.2, 0.12, 0.3, 0.18))),
    )

    assert agreement is LocationAgreement.AGREES


def test_a_label_without_a_box_only_asks_about_the_page() -> None:
    """FR-018 makes the box optional; a label with none asks a weaker question."""
    agreement = agreement_for(ExpectedLocation(page=PAGE), _outcome(geometry=None))

    assert agreement is LocationAgreement.AGREES


def test_a_label_with_no_location_at_all_yields_no_agreement() -> None:
    """``None``, not ``AGREES``. There is nothing to agree with.

    Reporting ``AGREES`` would inflate the mislocation denominator with fields
    nobody located, making the rate look better the *less* the dataset says.
    """
    assert agreement_for(None, _outcome()) is None


def test_containment_is_measured_against_the_recorded_area_not_the_label_box() -> None:
    """The IoU trap, asserted directly.

    A tight correct box inside a generous hand-drawn one has an IoU far below
    0.5 and a containment of 1.0. If this ever flips, the rule became IoU and
    every hand-labelled document in the dataset starts failing.
    """
    tiny = BBox(0.2, 0.12, 0.22, 0.14)
    agreement = agreement_for(
        ExpectedLocation(page=PAGE, bbox=LABEL_BOX), _outcome(geometry=_geometry(tiny))
    )

    label_area = (LABEL_BOX.x1 - LABEL_BOX.x0) * (LABEL_BOX.y1 - LABEL_BOX.y0)
    tiny_area = (tiny.x1 - tiny.x0) * (tiny.y1 - tiny.y0)
    assert tiny_area / label_area < CONTAINMENT_THRESHOLD, "the IoU-style ratio is low"
    assert agreement is LocationAgreement.AGREES, "and containment says it is right"


# -- DISAGREES ---------------------------------------------------------------


def test_a_value_on_the_wrong_page_disagrees() -> None:
    agreement = agreement_for(
        ExpectedLocation(page=1, bbox=LABEL_BOX), _outcome(pages=(0,), geometry=None)
    )

    assert agreement is LocationAgreement.DISAGREES


def test_a_same_page_wrong_location_value_disagrees() -> None:
    """The case the rule exists for: right page, wrong place, resolves cleanly."""
    agreement = agreement_for(
        ExpectedLocation(page=PAGE, bbox=LABEL_BOX),
        _outcome(geometry=_geometry(BBox(0.8, 0.8, 0.9, 0.9))),
    )

    assert agreement is LocationAgreement.DISAGREES


def test_geometry_that_covers_nothing_on_the_expected_page_disagrees() -> None:
    """``()`` is evidence of absence; ``None`` is absence of evidence (GRD-17).

    The parser did supply geometry, and none of it is where the label says. That
    is a real disagreement, and it must not be softened into NOT_ASSESSABLE.
    """
    agreement = agreement_for(
        ExpectedLocation(page=PAGE, bbox=LABEL_BOX),
        _outcome(pages=(PAGE,), geometry=_geometry(BBox(0.2, 0.12, 0.3, 0.18), page=1)),
    )

    assert agreement is LocationAgreement.DISAGREES


# -- NOT_ASSESSABLE ----------------------------------------------------------


def test_a_missing_geometry_case_is_not_assessable_never_disagrees() -> None:
    """The headline of EVA-14b.

    ``geometry is None`` means the parser supplied none. Calling that a
    disagreement makes the mislocation rate a property of the parser rather than
    of the extraction.
    """
    agreement = agreement_for(ExpectedLocation(page=PAGE, bbox=LABEL_BOX), _outcome(geometry=None))

    assert agreement is LocationAgreement.NOT_ASSESSABLE
    assert agreement is not LocationAgreement.DISAGREES


def test_an_ungrounded_value_is_not_assessable() -> None:
    """Ungrounded is not mislocated.

    It is already counted by the grounding rate; counting it here too would charge
    one failure twice, in two metrics a reader would reasonably add up.
    """
    agreement = agreement_for(
        ExpectedLocation(page=PAGE, bbox=LABEL_BOX),
        _outcome(status=GroundingStatus.UNGROUNDED, pages=(), geometry=None),
    )

    assert agreement is LocationAgreement.NOT_ASSESSABLE


def test_not_assessable_leaves_the_mislocation_denominator() -> None:
    """EVA-14c. The denominator is agrees + disagrees, and nothing else.

    Including not-assessable outcomes would make the rate fall whenever geometry
    became less available -- an improvement in the number produced by a loss of
    information.
    """
    from docdoc.evaluation.metrics import count_outcomes
    from docdoc.evaluation.outcomes import FieldOutcome
    from docdoc.evaluation.tiers import Tier

    outcomes = [
        FieldOutcome(
            document_id="d",
            field_path=f"f{index}",
            kind=FieldOutcomeKind.CORRECT,
            tier=Tier.PUBLIC,
            location_agreement=agreement,
        )
        for index, agreement in enumerate(
            [
                LocationAgreement.AGREES,
                LocationAgreement.DISAGREES,
                LocationAgreement.NOT_ASSESSABLE,
                LocationAgreement.NOT_ASSESSABLE,
            ]
        )
    ]
    counts = count_outcomes(outcomes, value_paths=set())

    assert (counts.agrees, counts.disagrees, counts.not_assessable) == (1, 1, 2)

    from docdoc.evaluation.metrics import _metrics_from

    mislocation = _metrics_from(counts, None)["mislocation_rate"]
    assert mislocation.denominator == 2, "the two not-assessable outcomes must be outside it"
    assert mislocation.numerator == 1


# -- the separate axis -------------------------------------------------------


def test_location_agreement_is_not_a_seventh_field_outcome() -> None:
    """The closed set stays at six. Location lives on its own field (EVA-14)."""
    assert len(list(FieldOutcomeKind)) == 6
    assert {str(value) for value in LocationAgreement} == {
        "agrees",
        "disagrees",
        "not_assessable",
    }
    assert not {str(k) for k in FieldOutcomeKind} & {str(v) for v in LocationAgreement}


@pytest.mark.parametrize(
    ("kind", "agreement"),
    [
        (FieldOutcomeKind.CORRECT, LocationAgreement.DISAGREES),
        (FieldOutcomeKind.INCORRECT, LocationAgreement.AGREES),
    ],
)
def test_a_value_can_be_correct_and_mislocated_or_wrong_and_located(
    kind: FieldOutcomeKind, agreement: LocationAgreement
) -> None:
    """Both combinations are representable, which is what "separate axis" means."""
    from docdoc.evaluation.outcomes import FieldOutcome
    from docdoc.evaluation.tiers import Tier

    outcome = FieldOutcome(
        document_id="d",
        field_path="total",
        kind=kind,
        tier=Tier.PUBLIC,
        location_agreement=agreement,
    )

    assert outcome.kind is kind
    assert outcome.location_agreement is agreement


def test_the_report_carries_location_agreement_only_where_a_label_stated_one() -> None:
    """End to end over the fixture, so the wiring is checked and not just the rule."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    located = [o for o in report.outcomes if o.location_agreement is not None]
    assert len(located) == 1, (
        "the fixture states exactly one expected location; more or fewer means the "
        "agreement is being invented for labels that did not ask for it"
    )
    assert located[0].field_path == "invoice_number"
    assert located[0].location_agreement is LocationAgreement.AGREES
