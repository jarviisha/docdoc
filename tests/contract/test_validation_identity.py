"""T068 — what moves the artifact id, and what must not (FR-048, SC-017).

Both directions are load-bearing. An input that can change a verdict and does not
reach the identity is the stale-cache bug ADR-0003 exists to close. An input that
cannot change a verdict and does reach it invalidates work for no reason, which is
how a chain stops being trusted.
"""

from __future__ import annotations

import logging

from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema

from docdoc.validation import (
    GroundingPolicy,
    Severity,
    ValidationOptions,
    validate,
)


def _run(schema=None, options=None, **kwargs):
    schema = schema or invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(schema=schema, **kwargs)
    return validate(pair.extraction, pair.grounding, schema, options=options)


class TestStability:
    def test_identical_inputs_give_an_identical_result(self) -> None:
        first, second = _run(), _run()
        assert first.artifact_id == second.artifact_id
        assert first.model_dump() == second.model_dump()

    def test_the_id_is_a_content_address(self) -> None:
        assert _run().artifact_id.startswith("sha256:")


class TestWhatMovesIt:
    def test_the_grounding_policy(self) -> None:
        strict = ValidationOptions(grounding_policy=GroundingPolicy(ungrounded=Severity.ERROR))
        assert _run().artifact_id != _run(options=strict).artifact_id

    def test_disabling_a_rule(self) -> None:
        subset = ValidationOptions(enabled_rules=frozenset({"due_after_issue"}))
        assert _run().artifact_id != _run(options=subset).artifact_id

    def test_editing_a_rule(self) -> None:
        """Through the **chain**, not through this stage's options hash.

        A rule's tolerance lives on the rule, so it is inside `schema_hash`,
        inside the extract stage's `options_hash`, inside the extraction artifact
        id, inside the grounding artifact id, and inside this one. Folding the
        rule body here as well would restate what the chain already carries —
        which is why `options_hash_for_validation` folds only the rule *ids*.
        """
        exact = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        tolerant = invoice_schema(rules=(rule_fixtures.sum_rule(tolerance="0.05"),))
        assert _run(schema=exact).artifact_id != _run(schema=tolerant).artifact_id

    def test_a_different_document(self) -> None:
        assert _run().artifact_id != _run(number="INV-2026-777").artifact_id

    def test_the_validator_version(self, monkeypatch) -> None:
        before = _run().artifact_id
        monkeypatch.setattr("docdoc.validation.identity.VALIDATOR_VERSION", "9.9.9")
        assert _run().artifact_id != before

    def test_the_rule_vocabulary_version(self, monkeypatch) -> None:
        before = _run().artifact_id
        monkeypatch.setattr(
            "docdoc.validation.identity.RULE_VOCABULARY_VERSION", "rule_vocabulary@2"
        )
        assert _run().artifact_id != before

    def test_the_pattern_dialect_version(self, monkeypatch) -> None:
        """The dialect decides what a `pattern` constraint means (VAL-30)."""
        before = _run().artifact_id
        monkeypatch.setattr(
            "docdoc.validation.identity.PATTERN_DIALECT_VERSION", "pattern_dialect@2"
        )
        assert _run().artifact_id != before


class TestWhatMustNotMoveIt:
    def test_naming_every_rule_explicitly(self) -> None:
        """ "All rules" and "these four, which are all of them" are the same run."""
        schema = invoice_schema(rules=rule_fixtures.every_kind())
        every = frozenset(rule.id for rule in schema.rules)
        named = _run(options=ValidationOptions(enabled_rules=every))
        assert _run().artifact_id == named.artifact_id

    def test_logging_configuration(self) -> None:
        before = _run().artifact_id
        logging.getLogger("docdoc.validation").setLevel(logging.CRITICAL)
        try:
            assert _run().artifact_id == before
        finally:
            logging.getLogger("docdoc.validation").setLevel(logging.NOTSET)

    def test_reading_the_result_in_a_different_order(self) -> None:
        result = _run()
        list(reversed(result.findings))
        list(reversed(result.checks))
        assert _run().artifact_id == result.artifact_id


class TestProvenance:
    def test_it_records_everything_needed_to_explain_the_verdict(self) -> None:
        """FR-049, SC-016 — readable without re-running anything."""
        schema = invoice_schema(rules=rule_fixtures.every_kind())
        pair = artifacts.build(schema=schema)
        result = validate(pair.extraction, pair.grounding, schema)
        provenance = result.provenance

        assert provenance.document_id == pair.document.id
        assert provenance.extraction_artifact_id == pair.extraction.artifact_id
        assert provenance.grounding_artifact_id == pair.grounding.artifact_id
        assert provenance.schema_identity == schema.identity
        assert provenance.schema_hash == pair.extraction.provenance.schema_hash
        assert provenance.rule_vocabulary_version == "rule_vocabulary@1"
        assert provenance.pattern_dialect_version == "pattern_dialect@1"
        assert provenance.enabled_rules == (
            "due_after_issue",
            "line_amount_is_quantity_times_price",
            "named_supplier_has_tax_id",
            "total_matches_lines",
        )
        assert provenance.validator_id == "deterministic-validator"
        assert provenance.validator_version

    def test_it_chains_from_the_grounding_artifact(self) -> None:
        """ADR-0003 — the id inherits the parse, the schema, the model, the threshold.

        **This assertion used to be circular**, and a mutation run found it: it
        recomputed the expected value with `validation_artifact_id_for` itself, so
        truncating the grounding artifact id *inside* that function moved both
        sides equally and the test passed. It proved that `validate()` calls the
        helper, and nothing about what the helper depends on.

        What is asserted now cannot be satisfied by a function that discards its
        input: two grounding ids differing only in their **last** characters must
        produce different validation ids.
        """
        from docdoc.validation.identity import (
            options_hash_for_validation,
            validation_artifact_id_for,
        )

        options_hash = options_hash_for_validation(ValidationOptions(), enabled_rules=())
        base = "sha256:" + "a" * 64
        tail_differs = "sha256:" + "a" * 63 + "b"

        first = validation_artifact_id_for(grounding_artifact_id=base, options_hash=options_hash)
        second = validation_artifact_id_for(
            grounding_artifact_id=tail_differs, options_hash=options_hash
        )
        assert first != second, (
            "the validation id must depend on the whole grounding artifact id; a "
            "truncation or a prefix would collide across documents"
        )
        assert (
            validation_artifact_id_for(grounding_artifact_id=base, options_hash=options_hash)
            == first
        )

    def test_the_chain_holds_end_to_end(self) -> None:
        """The same property through `validate()`, so the wiring is covered too.

        Two runs of one extraction against grounding results whose ids differ must
        produce different validation ids — otherwise a stored verdict could not say
        which grounding it judged.
        """
        schema = invoice_schema()
        pair = artifacts.build(schema=schema)
        other = artifacts.build(schema=schema, number="INV-2026-424")
        assert pair.grounding.artifact_id != other.grounding.artifact_id

        mine = validate(pair.extraction, pair.grounding, schema)
        theirs = validate(other.extraction, other.grounding, schema)
        assert mine.artifact_id != theirs.artifact_id
        assert mine.provenance.grounding_artifact_id == pair.grounding.artifact_id

    def test_the_order_of_an_enabled_rule_set_never_moves_the_hash(self) -> None:
        """T113, FR-048, FR-051 — a set's iteration order must not reach identity.

        `options_hash_for_validation` sorts what it is given, and every caller
        inside `validate()` already hands it a sorted tuple — so dropping the sort
        broke nothing, which left it looking like dead code. It is not: this
        function is reachable from outside `validate()`, and a hash that varied
        with a caller's iteration order would be exactly the reproducibility bug
        FR-051 forbids.
        """
        from docdoc.validation.identity import options_hash_for_validation

        forwards = ("alpha", "beta", "gamma")
        backwards = tuple(reversed(forwards))
        assert options_hash_for_validation(
            ValidationOptions(), enabled_rules=forwards
        ) == options_hash_for_validation(ValidationOptions(), enabled_rules=backwards)
        assert options_hash_for_validation(
            ValidationOptions(), enabled_rules=forwards
        ) != options_hash_for_validation(ValidationOptions(), enabled_rules=("alpha",))
