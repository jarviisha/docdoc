"""T006 — the datasets that must be refused, as the JSON a maintainer would write.

Every case here is a **manifest and label payload**, not a constructed model, and
that is the whole design of the fixture. Two of the seven -- an empty ``basis``
and a synthetic document that does not name its generator -- cannot be built as
Python objects at all, because ``DocumentOrigin`` refuses them in a validator. A
fixture that constructed models could therefore never express them, and the two
refusals most likely to be quietly lost are exactly the two it would have to skip.

Writing them as JSON also tests the thing that actually happens: a maintainer
adds a document by editing data (FR-022), and the mistake they make is a mistake
in data. Refused at **load** (FR-014), because an authoring error that survives
into a scoring run does not announce itself -- it becomes a document that
permanently scores zero for a reason nobody can see.

Each builder returns ``(manifest, labels)``; :func:`write_dataset` puts them on
disk in the layout ``load_golden_set`` reads.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tests.fixtures.evaluation.datasets import INVOICE, registry

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "CASES",
    "write_dataset",
]

Payload = tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]


def _origin(**overrides: Any) -> dict[str, Any]:
    origin = {
        "kind": "synthetic",
        "basis": "Synthetic, written for this test suite.",
        "generator_id": "tests.fixtures.evaluation.authoring_errors",
        "generator_version": "1.0.0",
    }
    origin.update(overrides)
    return origin


def _document(document_id: str, *, labels: int = 1, **overrides: Any) -> dict[str, Any]:
    entry = {
        "document_id": document_id,
        "blob_sha256": "sha256:" + "a" * 64,
        "tier": "public",
        "origin": _origin(),
        "schema_identity": INVOICE,
        "schema_hash": registry().resolve(INVOICE).schema_hash,
        "path": f"documents/{document_id}.txt",
        "declared_label_count": labels,
    }
    entry.update(overrides)
    return entry


def _label(field_path: str, value: Any) -> dict[str, Any]:
    return {"field_path": field_path, "expectation": "value", "value": value}


# -- the seven cases ---------------------------------------------------------


def duplicated_document_id() -> Payload:
    """The same id twice. A duplicate silently doubles its weight in every metric."""
    manifest = {"documents": [_document("dupe"), _document("dupe")]}
    return manifest, {"dupe": [_label("invoice_number", "INV-001")]}


def unresolvable_field_path() -> Payload:
    """A label addressing a field the schema does not declare.

    Not a harmless typo: the field can never be predicted, so it scores as
    permanently missing and drags a real metric down for a reason that lives in
    the dataset rather than in the pipeline.
    """
    manifest = {"documents": [_document("stray")]}
    return manifest, {"stray": [_label("invoice_nubmer", "INV-001")]}


def value_the_type_cannot_carry() -> Payload:
    """A ``decimal`` field labelled with a JSON number.

    A decimal travels as a string. Accepting the number would make the label a
    ``float``, and `comparators@1`'s type gate would then refuse to match it
    against every correctly extracted ``Decimal`` -- so the dataset would report
    a perfect pipeline as entirely wrong (EVA-5b, EVA-12a).
    """
    manifest = {"documents": [_document("mistyped")]}
    return manifest, {"mistyped": [_label("total", 1240.00)]}


def origin_with_empty_basis() -> Payload:
    """A document nobody stated a basis for. Not admitted (FR-011)."""
    manifest = {"documents": [_document("no-basis", origin=_origin(basis="   "))]}
    return manifest, {"no-basis": [_label("invoice_number", "INV-001")]}


def synthetic_without_generator() -> Payload:
    """A synthetic document whose generator is unknown.

    It cannot be regenerated, so its labels cannot be checked against the thing
    that produced the document they describe. Unverifiable truth is not truth
    (EVA-2b).
    """
    manifest = {
        "documents": [
            _document(
                "no-generator",
                origin=_origin(generator_id=None, generator_version=None),
            )
        ]
    }
    return manifest, {"no-generator": [_label("invoice_number", "INV-001")]}


def entry_key_naming_a_non_scalar() -> Payload:
    """An alignment key that names a group rather than a value.

    Nothing can be compared for equality against a group, so the alignment would
    silently fall back to matching nothing (EVA-4a).
    """
    manifest = {
        "documents": [_document("keyed")],
        "entry_keys": [{"group_path": "line_items", "key_field": "supplier"}],
    }
    return manifest, {"keyed": [_label("invoice_number", "INV-001")]}


def duplicate_key_values() -> Payload:
    """Two entries labelled with the same alignment key.

    Refused rather than resolved by a tie-break: any tie-break here would be an
    invented rule deciding which of two identical-looking entries the truth was
    about (EVA-13a).
    """
    manifest = {
        "documents": [_document("ambiguous", labels=2)],
        "entry_keys": [{"group_path": "line_items", "key_field": "description"}],
    }
    labels = {
        "ambiguous": [
            _label("line_items[0].description", "Widget"),
            _label("line_items[1].description", "Widget"),
        ]
    }
    return manifest, labels


#: Every case, with the fragment of the refusal message that must name the
#: offender. The fragment is asserted rather than the whole message: a test
#: pinned to exact prose fails on a wording improvement, and a test that only
#: checks the exception type passes when the error names the wrong document.
CASES: tuple[tuple[str, Any, str], ...] = (
    ("duplicated_document_id", duplicated_document_id, "dupe"),
    ("unresolvable_field_path", unresolvable_field_path, "invoice_nubmer"),
    ("value_the_type_cannot_carry", value_the_type_cannot_carry, "total"),
    ("origin_with_empty_basis", origin_with_empty_basis, "no-basis"),
    ("synthetic_without_generator", synthetic_without_generator, "no-generator"),
    ("entry_key_naming_a_non_scalar", entry_key_naming_a_non_scalar, "supplier"),
    ("duplicate_key_values", duplicate_key_values, "Widget"),
)


def write_dataset(root: Path, payload: Payload) -> Path:
    """Write a manifest and its labels to ``root``; return the manifest path."""
    manifest, labels = payload
    labels_dir = root / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for document_id, items in labels.items():
        name = document_id.replace(":", "_")
        (labels_dir / f"{name}.json").write_text(json.dumps(items, indent=2), encoding="utf-8")
    return manifest_path
