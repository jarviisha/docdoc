"""T046, T047, T049 — identity, independence, and the version guard (GRD-20, FR-036…FR-042).

Three jobs. The artifact id moves for every input that can change an outcome and
for nothing else. Re-grounding produces a new result rather than editing an old
one. And the **version guard**: a snapshot that fails the build when the algorithm
changes without `GROUNDING_VERSION` moving.

That last one is the load-bearing test in this file. ADR-0005 requires the bump,
and no system can detect a semantic change on its own -- so the snapshot converts
"someone must remember" into "the build stops".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docdoc.grounding import GroundingOptions, ground
from docdoc.grounding.identity import (
    GROUNDER_ID,
    GROUNDER_VERSION,
    GROUNDING_VERSION,
    grounding_artifact_id_for,
    options_hash_for_grounding,
)
from docdoc.grounding.match import MAX_ALTERNATIVES
from docdoc.grounding.options import DEFAULT_CANDIDATE_BUDGET, DEFAULT_THRESHOLD
from docdoc.grounding.view import MATCH_VIEW_VERSION
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Invoice INV-001\nTotal 1,240.00"
SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "grounding_version.json"


def build(artifact_id: str = "sha256:" + "e" * 64):
    doc = make_document(TEXT)
    extraction = make_extraction(
        {"f": make_extracted("f", value="x", claimed_text="INV-001")},
        document=doc,
        artifact_id=artifact_id,
    )
    return doc, extraction


class TestWhatMovesTheArtifactId:
    def test_the_threshold_moves_it(self) -> None:
        doc, extraction = build()
        a = ground(doc, extraction, options=GroundingOptions(threshold=0.90))
        b = ground(doc, extraction, options=GroundingOptions(threshold=0.95))
        assert a.artifact_id != b.artifact_id

    def test_the_candidate_budget_moves_it(self) -> None:
        """Unlike a timeout, reaching a budget changes the answer (GRD-19)."""
        doc, extraction = build()
        a = ground(doc, extraction, options=GroundingOptions(candidate_budget=1_500))
        b = ground(doc, extraction, options=GroundingOptions(candidate_budget=500))
        assert a.artifact_id != b.artifact_id

    def test_the_upstream_extraction_artifact_moves_it(self) -> None:
        a = ground(*build("sha256:" + "a" * 64))
        b = ground(*build("sha256:" + "b" * 64))
        assert a.artifact_id != b.artifact_id

    def test_the_algorithm_version_moves_it(self) -> None:
        options = GroundingOptions()
        real = options_hash_for_grounding(options)
        from docdoc.kernel import options_hash_for

        pretend = options_hash_for(
            {
                "grounding_version": "v2",
                "match_view_version": MATCH_VIEW_VERSION,
                "threshold": options.threshold,
                "candidate_budget": options.candidate_budget,
            }
        )
        assert real != pretend


class TestWhatMustNotMoveIt:
    def test_grounding_the_same_inputs_twice_gives_the_same_id(self) -> None:
        doc, extraction = build()
        assert ground(doc, extraction).artifact_id == ground(doc, extraction).artifact_id

    def test_and_the_same_outcomes(self) -> None:
        doc, extraction = build()
        assert ground(doc, extraction).outcomes == ground(doc, extraction).outcomes

    def test_the_id_chains_from_the_extraction_artifact(self) -> None:
        """ADR-0003 -- the chain is what makes the id inherit document, schema, and model."""
        doc, extraction = build()
        expected = grounding_artifact_id_for(
            extraction_artifact_id=extraction.artifact_id,
            options=GroundingOptions(),
        )
        assert ground(doc, extraction).artifact_id == expected


class TestProvenanceIsRecorded:
    def test_every_field_is_populated(self) -> None:
        doc, extraction = build()
        provenance = ground(doc, extraction).provenance
        assert provenance.document_id == doc.id
        assert provenance.extraction_artifact_id == extraction.artifact_id
        assert provenance.grounding_version == GROUNDING_VERSION
        assert provenance.match_view_version == MATCH_VIEW_VERSION
        assert provenance.grounder_id == GROUNDER_ID
        assert provenance.grounder_version == GROUNDER_VERSION
        assert provenance.options.threshold == DEFAULT_THRESHOLD
        assert provenance.view_id.startswith("sha256:")

    def test_the_view_identity_is_reachable_from_a_result(self) -> None:
        """FR-020's purpose clause, not just its existence clause.

        The identity was computed on every run and read by nobody: a consumer
        could not answer "did these two runs compare against the same folded
        text?" without re-deriving it from a formula they would first have to
        read the source to learn. Recording it is what makes it usable.
        """
        from docdoc.grounding.view import _view_id_for

        doc, extraction = build()
        provenance = ground(doc, extraction).provenance
        assert provenance.view_id == _view_id_for(
            provenance.document_id, provenance.match_view_version
        )

    def test_two_runs_over_one_document_agree_on_the_view_identity(self) -> None:
        doc, extraction = build()
        assert ground(doc, extraction).provenance.view_id == (
            ground(doc, extraction).provenance.view_id
        )

    def test_a_different_document_compared_against_a_different_view(self) -> None:
        from docdoc.grounding.view import MatchView

        a = MatchView.build(make_document(TEXT, data=b"%PDF-1.7 one"))
        b = MatchView.build(make_document(TEXT, data=b"%PDF-1.7 two"))
        assert a.view_id != b.view_id


class TestRegroundingIsIndependent:
    """FR-041 -- a new result with its own provenance; the prior one is untouched."""

    def test_reground_is_independent(self) -> None:
        doc, extraction = build()
        first = ground(doc, extraction)
        snapshot = (first.artifact_id, dict(first.outcomes), first.counts, first.provenance)

        second = ground(doc, extraction, options=GroundingOptions(threshold=0.5))

        assert (first.artifact_id, dict(first.outcomes), first.counts, first.provenance) == snapshot
        assert second.artifact_id != first.artifact_id
        assert second.provenance.options.threshold == 0.5
        assert first.provenance.options.threshold == DEFAULT_THRESHOLD

    def test_results_are_frozen(self) -> None:
        doc, extraction = build()
        result = ground(doc, extraction)
        with pytest.raises(ValidationError):
            result.artifact_id = "sha256:" + "0" * 64  # type: ignore[misc]


class TestTheVersionGuard:
    """T049 — the snapshot that turns a silent algorithm change into a failed build.

    ADR-0005 requires a `grounding_version` bump when the candidate generator, the
    scorer, the tie-break, the slack derivation, or the default threshold change.
    Nothing can detect a *semantic* change automatically, so this pins the values
    those decisions are expressed as. If it fails, that is the system asking which
    happened: a deliberate algorithm change needing a version bump, or an
    accidental edit needing reverting.
    """

    def current(self) -> dict[str, object]:
        return {
            "grounding_version": GROUNDING_VERSION,
            "match_view_version": MATCH_VIEW_VERSION,
            "grounder_id": GROUNDER_ID,
            "grounder_version": GROUNDER_VERSION,
            "default_threshold": DEFAULT_THRESHOLD,
            "default_candidate_budget": DEFAULT_CANDIDATE_BUDGET,
            "max_alternatives": MAX_ALTERNATIVES,
            "options_hash_at_defaults": options_hash_for_grounding(GroundingOptions()),
        }

    def test_the_pinned_algorithm_has_not_changed_without_a_version_bump(self) -> None:
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        assert self.current() == expected, (
            "A pinned grounding parameter moved. ADR-0005 requires `grounding_version` "
            "to bump whenever the candidate generator, the scorer, the tie-break, the "
            "slack derivation, or the default threshold changes, because results "
            "produced by two different algorithms must not carry the same version.\n\n"
            "If this was deliberate: bump GROUNDING_VERSION (and MATCH_VIEW_VERSION if "
            "a transformation changed), then update "
            f"{SNAPSHOT.relative_to(SNAPSHOT.parents[3])} and say which rule changed in "
            "the commit message.\n"
            "If this was not deliberate: revert it."
        )

    def test_the_options_hash_is_stable_across_runs(self) -> None:
        assert options_hash_for_grounding(GroundingOptions()) == options_hash_for_grounding(
            GroundingOptions()
        )
