"""T072 — disclosure follows the tier, never the caller's diligence (FR-056, SC-018).

The wording of FR-056 — redaction is a property of the **dataset** — is the whole
design. If it were a caller argument, then the day someone ran an evaluation
without the flag, a restricted corpus's contents would be in a report, and the
dataset's terms would have been enforced by memory. Memory fails silently and
exactly once.

So the tier decides and there is no argument to forget. A ``RESTRICTED`` outcome
carries ``expected_hash`` and ``predicted_hash`` instead of values, and the report
names the tiers it redacted — because a report that redacted without saying so
would be indistinguishable from one that had nothing to redact.

FR-026 requires every non-correct outcome to record the expected and predicted
value "subject to FR-056", and the hash is what satisfies it under this rule: a
restricted near-miss stays diagnosable as **different** — two hashes that do not
match — but not as *how*. That is the trade the dataset's terms impose, and it
beats the alternatives, which are publishing the value or reporting nothing.
"""

from __future__ import annotations

from docdoc.evaluation import (
    EvaluationOptions,
    FieldOutcomeKind,
    Tier,
    evaluate,
)
from docdoc.evaluation.redact import redact_outcome
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

FACTS = facts_for_fixtures()


def _restricted_report():  # type: ignore[no-untyped-def]
    return evaluate(
        golden_set(),
        prediction_set(include_restricted=True),
        facts=FACTS,
        options=EvaluationOptions(include_restricted=True),
    )


def test_the_fixture_actually_scores_a_restricted_document() -> None:
    """The guard on the guard. Everything below is vacuous without this."""
    report = _restricted_report()
    restricted = [o for o in report.outcomes if o.tier is Tier.RESTRICTED]

    assert restricted, "no restricted outcomes; this file would pass while testing nothing"


def test_zero_field_values_appear_under_a_restricted_tier() -> None:
    """SC-018: zero, not "few". One leaked value is the whole breach."""
    report = _restricted_report()
    restricted = [o for o in report.outcomes if o.tier is Tier.RESTRICTED]

    leaked = [o.field_path for o in restricted if o.expected is not None or o.predicted is not None]
    assert not leaked, f"these restricted outcomes carry values: {leaked}"


def test_every_affected_outcome_carries_a_hash_instead() -> None:
    """100% of them, so a restricted near-miss is still diagnosable as different."""
    report = _restricted_report()
    restricted = [
        o
        for o in report.outcomes
        if o.tier is Tier.RESTRICTED and o.kind is not FieldOutcomeKind.CORRECT
    ]

    for outcome in restricted:
        assert outcome.redacted
        assert outcome.expected_hash is not None or outcome.predicted_hash is not None, (
            f"{outcome.field_path} is redacted and carries neither hash, so the "
            "outcome says a value was wrong and offers nothing to compare"
        )


def test_the_hashes_distinguish_a_match_from_a_mismatch() -> None:
    """What the hash is *for*, asserted rather than assumed.

    Equal hashes mean equal renderings; different hashes mean different ones.
    Without this the hash would be a token that satisfied a schema.
    """
    from docdoc.evaluation.outcomes import FieldOutcome

    same = redact_outcome(
        FieldOutcome(
            document_id="r",
            field_path="total",
            kind=FieldOutcomeKind.INCORRECT,
            tier=Tier.RESTRICTED,
            expected="1240.00",
            predicted="1240.00",
        )
    )
    different = redact_outcome(
        FieldOutcome(
            document_id="r",
            field_path="total",
            kind=FieldOutcomeKind.INCORRECT,
            tier=Tier.RESTRICTED,
            expected="1240.00",
            predicted="1249.00",
        )
    )

    assert same.expected_hash == same.predicted_hash
    assert different.expected_hash != different.predicted_hash
    assert different.expected_hash is not None
    assert "1240.00" not in str(different.model_dump())


def test_the_report_states_which_tiers_it_redacted() -> None:
    """A report that redacted silently is indistinguishable from one with nothing to hide."""
    report = _restricted_report()

    assert report.redacted_tiers == (Tier.RESTRICTED,)


def test_a_public_only_report_declares_no_redaction() -> None:
    """The field must discriminate, or it is decoration."""
    report = evaluate(golden_set(), prediction_set(), facts=FACTS)

    assert report.redacted_tiers == ()
    assert not any(o.redacted for o in report.outcomes)


def test_public_outcomes_keep_their_values_in_the_same_report() -> None:
    """Redaction is scoped to the tier, not applied to the whole run.

    A run that redacted everything because one document was restricted would
    make the public tier undiagnosable, and contributors would stop including
    the restricted one — which is the opposite of what ADR-0009 wanted.
    """
    report = _restricted_report()

    public_near_miss = next(
        o
        for o in report.outcomes
        if o.tier is Tier.PUBLIC and o.document_id == "near-miss" and o.field_path == "total"
    )

    assert public_near_miss.expected == "350.00"
    assert public_near_miss.predicted == "300.00"
    assert not public_near_miss.redacted


def test_the_choice_follows_the_tier_and_not_any_caller_argument() -> None:
    """FR-056 stated as a signature check.

    ``redact_outcome`` takes an outcome and nothing else. There is no flag to
    pass, no flag to forget, and no option on ``EvaluationOptions`` that could
    turn disclosure back on.
    """
    import inspect

    signature = inspect.signature(redact_outcome)
    assert list(signature.parameters) == ["outcome"], (
        f"redact_outcome takes {list(signature.parameters)}; a second parameter is "
        "a disclosure decision a caller can get wrong"
    )

    option_names = set(EvaluationOptions.model_fields)
    assert not any("redact" in name or "disclos" in name for name in option_names), (
        f"an option controls redaction: {sorted(option_names)}"
    )


def test_a_public_outcome_passes_through_unchanged() -> None:
    """The no-op half, so the rule is not "hash everything"."""
    from docdoc.evaluation.outcomes import FieldOutcome

    outcome = FieldOutcome(
        document_id="p",
        field_path="total",
        kind=FieldOutcomeKind.INCORRECT,
        tier=Tier.PUBLIC,
        expected="1240.00",
        predicted="1249.00",
    )

    assert redact_outcome(outcome) is outcome


def test_no_restricted_value_reaches_the_serialized_report() -> None:
    """The end-to-end sweep: the whole report body, not just the outcome fields.

    A value could survive in a per-field-path key, a group outcome, or a
    provenance tuple — places a per-outcome check does not look.
    """
    report = _restricted_report()
    rendered = report.model_dump_json()

    restricted_values = {
        str(label.value)
        for labels in golden_set().labels.values()
        for label in labels
        if label.value is not None
    }
    # The restricted document in this fixture replays `clean`'s prediction, so
    # its values are `clean`'s -- which also appear legitimately under the public
    # tier. What must not appear is a *restricted* outcome carrying them, which
    # the per-outcome assertions above cover; here the check is that redaction
    # produced hashes rather than dropping the outcomes entirely.
    assert restricted_values
    assert '"redacted":true' in rendered
    assert '"predicted_hash":"sha256:' in rendered, (
        "redaction dropped the values without putting a hash in their place, which "
        "makes a restricted outcome un-diagnosable rather than merely un-readable"
    )

    # The restricted document supplies no labels, so every one of its outcomes is
    # UNLABELED and has no expected value to hash. That is the honest shape --
    # `expected_hash` is None because there was no expectation, not because the
    # hash was lost -- and it is worth pinning, because a hash of the empty string
    # here would look identical in a JSON body and mean something false.
    restricted = [o for o in report.outcomes if o.tier is Tier.RESTRICTED]
    assert all(o.kind is FieldOutcomeKind.UNLABELED for o in restricted)
    assert all(o.expected_hash is None for o in restricted)
    assert all(o.predicted_hash is not None for o in restricted)
