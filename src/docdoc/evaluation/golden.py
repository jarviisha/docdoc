"""The golden set: documents, labels, and the refusals that happen at load.

Every check in this module fires when the dataset loads, before a single metric is
computed. That timing is the requirement (FR-014), and the reason is specific: an
authoring error that survives into a scoring run does not announce itself as an
error. It becomes a document that permanently scores zero for reasons nobody can
see, and the number it produces looks exactly like a real measurement.

The one field worth reading twice is ``declared_label_count``, which every
document carries **including restricted ones whose labels are not present**. It is
what lets a partial report state its covered fraction as exact integers rather
than a guess -- see :class:`GoldenSet.tier_counts` and EVA-5a.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from docdoc.evaluation.errors import EvaluationError, naming
from docdoc.evaluation.labels import Expectation, Label
from docdoc.evaluation.tiers import DocumentOrigin, Tier
from docdoc.evaluation.values import carry, strip_indices

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from docdoc.evaluation.values import SchemaFacts

__all__ = [
    "EntryKeySpec",
    "GoldenDocument",
    "GoldenSet",
    "load_golden_set",
]


class EntryKeySpec(BaseModel):
    """How a repeating group's entries align (EVA-4).

    Declared by the **golden set**, never by the extraction schema. A key in the
    schema would move ``schema_hash`` under ADR-0008, and FR-004 would then refuse
    every label already written against that schema -- so the act of declaring a
    key to fix alignment would invalidate the dataset it was meant to measure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_path: str
    key_field: str


class GoldenDocument(BaseModel):
    """One document in the set (EVA-3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    blob_sha256: str
    tier: Tier
    origin: DocumentOrigin
    schema_identity: str
    schema_hash: str

    #: Repository-relative. Required for the public tier, forbidden for the
    #: restricted one -- a restricted document is referenced by hash precisely
    #: because it is not in the tree.
    path: str | None = None

    #: How many labels this document carries, committed even when the labels
    #: themselves are not (EVA-5a). Without it a checkout knows how many
    #: restricted documents exist but not how many labels they hold, so FR-015's
    #: "covered fraction" would have to be estimated.
    declared_label_count: int = 0


class GoldenSet(BaseModel):
    """A versioned collection of documents and labels (EVA-5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: tuple[GoldenDocument, ...]
    labels: dict[str, tuple[Label, ...]]
    entry_keys: tuple[EntryKeySpec, ...] = ()
    golden_set_id: str = ""

    def document(self, document_id: str) -> GoldenDocument | None:
        for candidate in self.documents:
            if candidate.document_id == document_id:
                return candidate
        return None

    def labels_for(self, document_id: str) -> tuple[Label, ...]:
        return self.labels.get(document_id, ())

    def key_for(self, group_path: str) -> str | None:
        for spec in self.entry_keys:
            if spec.group_path == group_path:
                return spec.key_field
        return None

    def tier_counts(self) -> dict[Tier, tuple[int, int]]:
        """``{tier: (documents, declared labels)}``, never merged across tiers.

        FR-009 requires the manifest to state its size per tier. Merging them
        would make the target size of quality gate 5 unreadable, because that gate
        counts the public tier alone -- CI cannot see the other one.
        """
        counts: dict[Tier, tuple[int, int]] = {}
        for document in self.documents:
            documents, labels = counts.get(document.tier, (0, 0))
            counts[document.tier] = (
                documents + 1,
                labels + document.declared_label_count,
            )
        return counts


def _refuse(message: str, **context: str | None) -> EvaluationError:
    return EvaluationError(message, **context)


def _resolve_path(field_path: str, schema_paths: frozenset[str]) -> bool:
    """Whether a label's path names a field the schema declares.

    Entry indices are stripped before the comparison: a schema declares
    ``line_items.amount`` once, while labels address ``line_items[0].amount`` and
    ``line_items[1].amount`` separately.
    """
    stripped = strip_indices(field_path)
    return stripped in schema_paths


def validate_golden_set(
    golden: GoldenSet, *, facts: SchemaFacts | None = None, dataset: str | None = None
) -> None:
    """Every load-time refusal of EVA-5b, each naming the offender.

    ``facts`` carries what the schemas declare -- their field paths, which of
    those are scalars, and each one's type. It is optional so a caller holding no
    schema registry can still load a dataset; where it is supplied, unresolvable
    label paths, non-scalar entry keys, and values a declared ``FieldType``
    cannot carry are all refused.

    ``dataset`` names the set at fault on every error raised here (FR-060). It
    falls back to the set's own identity, which is empty at load -- the identity
    is computed *from* the labels being checked, so it cannot exist before they
    are known good. :func:`load_golden_set` passes the manifest path instead.
    """
    with naming(dataset or golden.golden_set_id or None):
        _validate(golden, facts)


def _validate(golden: GoldenSet, facts: SchemaFacts | None) -> None:
    seen: set[str] = set()
    for document in golden.documents:
        if document.document_id in seen:
            raise _refuse(
                f"document {document.document_id!r} appears twice in the golden "
                "set; a duplicated document silently doubles its weight in every "
                "metric",
                document_id=document.document_id,
            )
        seen.add(document.document_id)

        if document.tier is Tier.PUBLIC and not document.path:
            raise _refuse(
                f"public-tier document {document.document_id!r} declares no path; "
                "the public tier is vendored into the repository",
                document_id=document.document_id,
            )
        if document.tier is Tier.RESTRICTED and document.path:
            raise _refuse(
                f"restricted-tier document {document.document_id!r} declares a "
                f"path ({document.path!r}); a restricted document is referenced "
                "by hash because it is not in the tree",
                document_id=document.document_id,
            )

    for document_id in golden.labels:
        if document_id not in seen:
            raise _refuse(
                f"labels supplied for {document_id!r}, which the golden set does not contain",
                document_id=document_id,
            )

    for document in golden.documents:
        labels = golden.labels_for(document.document_id)
        declared = document.declared_label_count
        if labels and len(labels) != declared:
            raise _refuse(
                f"document {document.document_id!r} declares "
                f"{declared} labels but {len(labels)} were supplied; a bundle "
                "short of its declaration is a smaller denominator wearing a full "
                "report's clothes",
                document_id=document.document_id,
                expected=str(declared),
                actual=str(len(labels)),
            )

        label_paths: set[str] = set()
        for label in labels:
            if label.field_path in label_paths:
                raise _refuse(
                    f"document {document.document_id!r} carries two labels for "
                    f"{label.field_path!r}",
                    document_id=document.document_id,
                    field_path=label.field_path,
                )
            label_paths.add(label.field_path)

            if facts is None:
                continue
            declared_paths = facts.paths.get(document.schema_identity)
            if declared_paths is not None and not _resolve_path(label.field_path, declared_paths):
                raise _refuse(
                    f"label path {label.field_path!r} does not resolve under "
                    f"schema {document.schema_identity}",
                    document_id=document.document_id,
                    field_path=label.field_path,
                )
            _refuse_uncarriable_value(document, label, facts)

    _validate_entry_keys(golden, None if facts is None else facts.scalar_paths)


def _refuse_uncarriable_value(document: GoldenDocument, label: Label, facts: SchemaFacts) -> None:
    """EVA-5b's fourth clause: a value the declared ``FieldType`` cannot hold.

    Checked even though :func:`load_golden_set` has already coerced the value on
    the way in, because a ``GoldenSet`` can be built in memory as well as read
    from disk -- and a check that only fires on one of those two paths is a check
    somebody will route around without meaning to.
    """
    if label.expectation is not Expectation.VALUE:
        return
    declared_type = facts.types_for(document.schema_identity).get(strip_indices(label.field_path))
    if declared_type is None:
        return
    try:
        carry(label.value, declared_type)
    except ValueError as exc:
        raise _refuse(
            f"label for {label.field_path!r} on document {document.document_id!r} "
            f"states a value the schema cannot carry: {exc}",
            document_id=document.document_id,
            field_path=label.field_path,
            expected=declared_type,
            actual=type(label.value).__name__,
        ) from exc


def _validate_entry_keys(
    golden: GoldenSet, scalar_paths: Mapping[str, frozenset[str]] | None
) -> None:
    groups: set[str] = set()
    for spec in golden.entry_keys:
        if spec.group_path in groups:
            raise _refuse(
                f"repeating group {spec.group_path!r} declares two alignment keys",
                field_path=spec.group_path,
            )
        groups.add(spec.group_path)

        if scalar_paths is None:
            continue
        qualified = f"{spec.group_path}.{spec.key_field}"
        for document in golden.documents:
            scalars = scalar_paths.get(document.schema_identity)
            if scalars is None:
                continue
            if qualified not in scalars:
                raise _refuse(
                    f"entry key {spec.key_field!r} is not a scalar field of "
                    f"{spec.group_path!r} under schema {document.schema_identity}",
                    document_id=document.document_id,
                    field_path=qualified,
                )

    _refuse_duplicate_expected_keys(golden)


def _refuse_duplicate_expected_keys(golden: GoldenSet) -> None:
    """A key that repeats within one document's labels makes alignment ambiguous.

    The predicted side is checked where it becomes knowable -- in
    :func:`docdoc.evaluation.alignment.align`, at scoring time, because nothing
    at load has seen a prediction. The expected side **is** knowable now, and
    checking it here is what makes it an authoring error rather than a scoring
    failure: the dataset is wrong, and the person who can fix it is the person
    loading it (EVA-13a, FR-014).
    """
    for spec in golden.entry_keys:
        for document_id, labels in sorted(golden.labels.items()):
            seen: dict[Any, str] = {}
            for label in labels:
                parsed = _entry_key_path(label.field_path, spec.group_path, spec.key_field)
                if parsed is None:
                    continue
                if label.value in seen:
                    raise _refuse(
                        f"repeating group {spec.group_path!r} aligns by "
                        f"{spec.key_field!r}, but document {document_id!r} labels "
                        f"the key {label.value!r} twice ({seen[label.value]} and "
                        f"{label.field_path}); alignment would have to guess which "
                        "entry the truth describes",
                        document_id=document_id,
                        field_path=f"{spec.group_path}.{spec.key_field}",
                        actual=str(label.value),
                    )
                seen[label.value] = label.field_path


def _entry_key_path(field_path: str, group_path: str, key_field: str) -> str | None:
    """``line_items[2].sku`` under group ``line_items`` keyed by ``sku`` -> the path."""
    if not field_path.startswith(f"{group_path}[") or not field_path.endswith(f".{key_field}"):
        return None
    return field_path


def _document_from_json(raw: Mapping[str, Any]) -> GoldenDocument:
    """One manifest entry, refused as an :class:`EvaluationError` if malformed.

    The model's own validators already refuse a document whose provenance cannot
    be stated -- an empty ``basis``, a synthetic document that does not name its
    generator (FR-011, EVA-2a, EVA-2b). What this adds is the **error type and
    the offender's name**: a raw pydantic ``ValidationError`` escaping the loader
    would make a dataset authoring mistake indistinguishable from a bug in
    docdoc, and would not carry the ``document_id`` a caller needs to find it.
    """
    try:
        return GoldenDocument.model_validate(raw)
    except Exception as exc:
        raise _refuse(
            f"manifest entry for document {raw.get('document_id', '<unnamed>')!r} "
            f"is not admissible: {exc}",
            document_id=str(raw.get("document_id", "")) or None,
        ) from exc


def _entry_key_from_json(raw: Mapping[str, Any]) -> EntryKeySpec:
    try:
        return EntryKeySpec.model_validate(raw)
    except Exception as exc:
        raise _refuse(
            f"manifest declares an alignment key that is not well formed: {exc}",
            field_path=str(raw.get("group_path", "")) or None,
        ) from exc


def _label_from_json(
    raw: Mapping[str, Any],
    *,
    document_id: str,
    types: Mapping[str, str] | None = None,
) -> Label:
    """Build one label from its authored JSON, typed as the schema declares.

    The coercion is the load-bearing part. A label authored as ``"1240.00"`` for
    a ``decimal`` field becomes ``Decimal("1240.00")`` here, which is the only
    reason it can ever match a prediction: `comparators@1` gates on type identity
    (EVA-12a), so an uncoerced label would compare unequal to a perfectly
    extracted value and the report would call the extraction wrong.
    """
    field_path = str(raw.get("field_path", ""))
    payload = dict(raw)
    declared_type = (types or {}).get(strip_indices(field_path))
    if declared_type is not None and payload.get("value") is not None:
        try:
            payload["value"] = carry(payload["value"], declared_type)
        except ValueError as exc:
            raise _refuse(
                f"label for {field_path!r} on document {document_id!r} states a "
                f"value the schema cannot carry: {exc}",
                document_id=document_id,
                field_path=field_path or None,
                expected=declared_type,
                actual=type(payload["value"]).__name__,
            ) from exc
    try:
        return Label.model_validate(payload)
    except EvaluationError:
        raise
    except Exception as exc:
        raise _refuse(
            f"label for document {document_id!r} is not well formed: {exc}",
            document_id=document_id,
            field_path=field_path or None,
        ) from exc


def load_golden_set(
    manifest_path: str | Path,
    *,
    labels_dir: str | Path | None = None,
    restricted_bundle: str | Path | None = None,
    facts: SchemaFacts | None = None,
) -> GoldenSet:
    """Read a manifest and its labels from disk, refusing anything malformed.

    ``restricted_bundle`` points at labels for the restricted tier, which the
    repository does not carry. Where it is absent, restricted documents load with
    their identity, provenance, and declared label count and no labels -- which is
    exactly what a partial report needs in order to state what it skipped.
    """
    manifest_file = Path(manifest_path)
    with naming(str(manifest_file)):
        return _load(manifest_file, labels_dir, restricted_bundle, facts)


def _load(
    manifest_file: Path,
    labels_dir: str | Path | None,
    restricted_bundle: str | Path | None,
    facts: SchemaFacts | None,
) -> GoldenSet:
    root = manifest_file.parent
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    documents = tuple(_document_from_json(entry) for entry in manifest.get("documents", ()))
    entry_keys = tuple(_entry_key_from_json(entry) for entry in manifest.get("entry_keys", ()))

    label_root = Path(labels_dir) if labels_dir is not None else root / "labels"
    labels: dict[str, tuple[Label, ...]] = {}
    for document in documents:
        if document.tier is Tier.RESTRICTED:
            continue
        label_file = label_root / f"{_safe_name(document.document_id)}.json"
        if not label_file.exists():
            continue
        raw = json.loads(label_file.read_text(encoding="utf-8"))
        labels[document.document_id] = tuple(
            _label_from_json(
                item,
                document_id=document.document_id,
                types=None if facts is None else facts.types_for(document.schema_identity),
            )
            for item in raw
        )

    if restricted_bundle is not None:
        labels.update(_load_restricted(Path(restricted_bundle), documents, facts))

    golden = GoldenSet(
        documents=tuple(sorted(documents, key=lambda d: d.document_id)),
        labels=labels,
        entry_keys=entry_keys,
    )
    validate_golden_set(golden, facts=facts, dataset=str(manifest_file))
    return golden


def _load_restricted(
    bundle: Path,
    documents: Iterable[GoldenDocument],
    facts: SchemaFacts | None = None,
) -> dict[str, tuple[Label, ...]]:
    """Resolve a caller-supplied restricted bundle against the manifest by hash.

    The bundle is keyed by ``blob_sha256`` rather than by ``document_id`` because
    the content hash is the only thing the manifest can state about a document it
    does not carry. A bundle naming a blob the manifest does not contain is
    refused: the two sides are describing different corpora, and scoring one
    against the other would report a number about neither (EVA-5b).

    **The declared-count check is not here.** It belongs to the whole set rather
    than to one bundle -- ``validate_golden_set`` compares every document's
    supplied labels against its ``declared_label_count``, so a short bundle is
    refused there whether it arrived through this path or any other (EVA-5a).
    """
    by_hash = {d.blob_sha256: d for d in documents if d.tier is Tier.RESTRICTED}
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    resolved: dict[str, tuple[Label, ...]] = {}
    for blob_sha256, raw_labels in payload.items():
        document = by_hash.get(blob_sha256)
        if document is None:
            raise _refuse(
                f"restricted bundle supplies labels for blob {blob_sha256!r}, "
                "which the manifest does not contain",
                actual=blob_sha256,
            )
        resolved[document.document_id] = tuple(
            _label_from_json(
                item,
                document_id=document.document_id,
                types=None if facts is None else facts.types_for(document.schema_identity),
            )
            for item in raw_labels
        )
    return resolved


def _safe_name(document_id: str) -> str:
    """A document id as a filename, with the ``sha256:`` prefix folded away."""
    return document_id.replace(":", "_")


def value_labels(labels: Iterable[Label]) -> tuple[Label, ...]:
    """The V partition of EVA-17: labels stating a value."""
    return tuple(item for item in labels if item.expectation is Expectation.VALUE)


def absence_labels(labels: Iterable[Label]) -> tuple[Label, ...]:
    """The A partition of EVA-17: labels asserting a correct absence."""
    return tuple(item for item in labels if item.expectation is Expectation.ABSENT)
