"""The report, its options, its provenance, and its declaration of what it missed.

Three refusals live here rather than in the scorer, because each is a statement
about whether a report may exist at all:

- a run that cannot record every provenance field FR-040 lists is **refused**
  rather than reported (EVA-21a);
- a partial run must say so (FR-001), which is why ``partial`` is a field on the
  model rather than something a caller infers;
- re-evaluating produces a new report and mutates nothing (FR-044), which frozen
  models give for free.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from docdoc.evaluation.alignment import ENTRY_ALIGNMENT_VERSIONS, GroupOutcome
from docdoc.evaluation.comparators import COMPARATOR_VERSIONS
from docdoc.evaluation.definitions import METRIC_DEFINITION_VERSION
from docdoc.evaluation.errors import EvaluationError, naming
from docdoc.evaluation.location import LOCATION_RULE_VERSION
from docdoc.evaluation.metrics import DatasetMetrics, DocumentScore
from docdoc.evaluation.outcomes import FieldOutcome
from docdoc.evaluation.tiers import Tier

__all__ = [
    "EvaluationOptions",
    "EvaluationProvenance",
    "EvaluationReport",
    "PartialDeclaration",
    "TierSize",
]


class EvaluationOptions(BaseModel):
    """Everything that can move a number, folded into ``options_hash`` (EVA-22).

    **The location threshold is not here.** It is fixed by
    ``location_rule_version``: ``page_box@1`` means containment >= 0.5, and
    changing the number is ``page_box@2``. A caller-settable threshold would let
    two reports both claiming ``page_box@1`` disagree about the mislocation rate,
    and the comparison refusal does not read it -- so ``compare()`` would diff
    them silently and the delta would mean nothing (EVA-22a).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_definition_version: str = METRIC_DEFINITION_VERSION
    comparator_versions: dict[str, str] = COMPARATOR_VERSIONS
    entry_alignment_version: str = ENTRY_ALIGNMENT_VERSIONS[0]
    location_rule_version: str = LOCATION_RULE_VERSION

    #: Whether the restricted tier was included. Identity-bearing because a run
    #: over more documents is a different measurement, not a bigger one.
    include_restricted: bool = False


class EvaluationProvenance(BaseModel):
    """Everything FR-040 requires a report to record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_revision: str
    golden_set_id: str
    prediction_set_id: str
    schema_identities: tuple[str, ...]
    schema_hashes: tuple[str, ...]
    prompt_hashes: tuple[str, ...]
    model_ids: tuple[str, ...]
    model_versions: tuple[str, ...]
    parser_ids: tuple[str, ...]
    parser_versions: tuple[str, ...]
    grounding_versions: tuple[str, ...]
    validator_versions: tuple[str, ...]
    scorer_id: str
    scorer_version: str
    metric_definition_version: str
    comparator_versions: dict[str, str]
    entry_alignment_version: str
    location_rule_version: str
    options: EvaluationOptions

    def refuse_if_incomplete(self, *, documents_considered: int = 1) -> None:
        """A metric whose origin is unknown is not evidence (FR-041, EVA-21a).

        Refusing beats recording a null. A null in a provenance field reads as
        "this run had no model", which is a claim, and a false one.

        ``documents_considered`` separates the two ways a list-valued provenance
        field can come back empty, and they are not the same failure. A run over
        **no documents** genuinely involved no schema and no model: "none" is a
        complete and true answer, and refusing it would mean an empty dataset
        raises instead of reporting that it measured nothing. A run over some
        documents that recorded **no** schema identity has lost something it
        should have, and that is the case this refuses.
        """
        with naming(self.golden_set_id or None):
            self._check(documents_considered)

    def _check(self, documents_considered: int) -> None:
        for name in (
            "repo_revision",
            "golden_set_id",
            "prediction_set_id",
            "scorer_id",
            "scorer_version",
            "metric_definition_version",
            "entry_alignment_version",
            "location_rule_version",
        ):
            if not getattr(self, name):
                raise EvaluationError(
                    f"cannot produce a report: provenance field {name!r} is "
                    "unknown. A metric whose origin cannot be stated is the vague "
                    "quality claim Principle IX rejects as evidence (FR-041)",
                    field_path=name,
                )
        if documents_considered == 0:
            return
        for name in ("schema_identities", "schema_hashes"):
            if not getattr(self, name):
                raise EvaluationError(
                    f"cannot produce a report: {documents_considered} document(s) "
                    f"were scored but provenance field {name!r} is empty, so the "
                    "report cannot say what was measured (FR-041)",
                    field_path=name,
                    actual=str(documents_considered),
                )


class TierSize(BaseModel):
    """One tier's size, as documents and labelled fields (FR-009, EVA-5a)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: Tier
    documents: int
    labelled_fields: int


class PartialDeclaration(BaseModel):
    """What a run did not evaluate, in exact integers (EVA-27)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skipped_documents: tuple[str, ...] = ()
    skipped_tiers: tuple[Tier, ...] = ()
    covered_documents: int = 0
    declared_documents: int = 0

    #: The declared counts come from the manifest, which commits them for the
    #: restricted tier even though it cannot commit its labels (EVA-5a). That is
    #: what makes the covered fraction exact rather than estimated.
    covered_labels: int = 0
    declared_labels: int = 0

    @property
    def covered_fraction(self) -> float | None:
        if self.declared_labels == 0:
            return None
        return self.covered_labels / self.declared_labels


class EvaluationReport(BaseModel):
    """One evaluation run's output (EVA-23)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: tuple[FieldOutcome, ...]
    document_scores: tuple[DocumentScore, ...]
    metrics: DatasetMetrics

    #: The golden set's size, **per tier and never merged** (FR-009). On *every*
    #: report, complete or partial, because the requirement's own justification is
    #: that the constitution's fifth quality gate turns blocking at a target size
    #: -- and a target nobody can read off a report is a target nobody can apply.
    #:
    #: Merged totals would make that gate unreadable: it counts the public tier
    #: alone, because CI cannot see the other one.
    #:
    #: This describes the **dataset**, not the run. It therefore includes tiers the
    #: run skipped -- what the run *covered* is :attr:`partial`'s business, and
    #: conflating the two would leave a reader unable to tell a small dataset from
    #: a large one measured narrowly.
    dataset_size: tuple[TierSize, ...] = ()

    #: ``None`` when the run covered the whole dataset. FR-001 forbids a partial
    #: report that is not marked partial, so this field exists from the first
    #: version -- adding it later would have meant every earlier report silently
    #: claimed to be complete.
    partial: PartialDeclaration | None = None

    redacted_tiers: tuple[Tier, ...] = ()
    provenance: EvaluationProvenance
    report_id: str

    # -- navigation (EVA-19, EVA-23) -----------------------------------------
    #
    # The data model describes a report as carrying its group outcomes and its
    # validation verdicts directly, and a `DocumentScore` as carrying its own
    # outcomes and group outcomes. The **storage** is normalized instead: outcomes
    # live in one flat, totally ordered tuple, and every `FieldOutcome` and
    # `GroupOutcome` already names its `document_id`.
    #
    # That is deliberate, and the accessors below are what make it invisible.
    # Storing each document's outcomes on its `DocumentScore` as well would put
    # every outcome in the serialized report twice -- doubling the bytes FR-043
    # requires to be identical, and pushing on the performance bound -- to save a
    # filter the report can perform itself.

    @property
    def group_outcomes(self) -> tuple[GroupOutcome, ...]:
        """Every repeating group's entry-level facts (EVA-23, FR-021)."""
        return self.metrics.group_outcomes

    @property
    def validation_verdicts(self) -> dict[str, int]:
        """Milestone 5's verdict distribution, reused not recomputed (EVA-23, FR-034)."""
        return self.metrics.validation_verdicts

    def outcomes_for(self, document_id: str) -> tuple[FieldOutcome, ...]:
        """One document's field outcomes, in the report's total order (EVA-19).

        This is the answer to "which fields were wrong on *this* document?" --
        the question a maintainer reading a per-document score asks next, and the
        one FR-055 requires to be answerable without re-running anything.
        """
        return tuple(outcome for outcome in self.outcomes if outcome.document_id == document_id)

    def groups_for(self, document_id: str) -> tuple[GroupOutcome, ...]:
        """One document's repeating groups (EVA-19).

        Where an entry count mismatched, this is what says so as its own fact
        rather than leaving a reader to infer it from the field-level wreckage
        positional alignment produces downstream of a missing entry (FR-021).
        """
        return tuple(
            group for group in self.metrics.group_outcomes if group.document_id == document_id
        )
