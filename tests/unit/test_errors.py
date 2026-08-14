"""T007 — error hierarchy and structured attributes (FR-023)."""

from __future__ import annotations

import pytest

from docdoc.kernel import (
    BBox,
    CapabilityError,
    DocdocError,
    DocumentInvariantError,
    GeometryError,
    IdentityError,
    KernelError,
    MergeError,
    Span,
    SpanError,
)

ALL_KERNEL_ERRORS = [
    SpanError,
    GeometryError,
    DocumentInvariantError,
    MergeError,
    CapabilityError,
    IdentityError,
]


@pytest.mark.parametrize("error_type", ALL_KERNEL_ERRORS)
def test_every_kernel_error_descends_from_the_roots(error_type: type[Exception]) -> None:
    assert issubclass(error_type, KernelError)
    assert issubclass(error_type, DocdocError)
    assert issubclass(error_type, Exception)


def test_a_single_root_catches_everything_docdoc_raises() -> None:
    with pytest.raises(DocdocError):
        raise SpanError("boom", span=Span(0, 1), text_length=0)


def test_span_error_carries_the_offending_span() -> None:
    error = SpanError("out of range", span=Span(5, 99), text_length=10)
    assert error.span == Span(5, 99)
    assert error.text_length == 10


def test_geometry_error_carries_the_offending_box() -> None:
    error = GeometryError("out of unit square", bbox=BBox(0.0, 0.0, 1.5, 1.0), page_index=2)
    assert error.bbox == BBox(0.0, 0.0, 1.5, 1.0)
    assert error.page_index == 2


def test_document_invariant_error_names_the_rule() -> None:
    error = DocumentInvariantError(
        "tokens overlap", rule="DOC-3", detail="token 4 overlaps token 5"
    )
    assert error.rule == "DOC-3"
    assert "token 4" in error.detail


def test_merge_error_carries_a_machine_readable_reason() -> None:
    error = MergeError(
        "parts differ", reason="mismatched_source", part_ids=("sha256:a", "sha256:b")
    )
    assert error.reason == "mismatched_source"
    assert len(error.part_ids) == 2


def test_capability_error_names_the_capability_and_availability() -> None:
    """The mechanism enforcing the no-silent-fallback rule (FR-022)."""
    error = CapabilityError(
        "geometry unavailable", capability="geometry", available=False, parser_id="pdf_text"
    )
    assert error.capability == "geometry"
    assert error.available is False
    assert error.parser_id == "pdf_text"


def test_identity_error_names_the_field() -> None:
    error = IdentityError("non-finite float", field="options.dpi", detail="NaN is not encodable")
    assert error.field == "options.dpi"


@pytest.mark.parametrize("error_type", ALL_KERNEL_ERRORS)
def test_errors_render_a_useful_message(error_type: type[Exception]) -> None:
    """Structured attributes are required, but the message must still be readable."""
    kwargs: dict[str, object] = {
        SpanError: {"span": Span(0, 1), "text_length": 0},
        GeometryError: {"bbox": None, "page_index": None},
        DocumentInvariantError: {"rule": "DOC-1", "detail": ""},
        MergeError: {"reason": "no_parts", "part_ids": ()},
        CapabilityError: {"capability": "geometry", "available": False, "parser_id": "p"},
        IdentityError: {"field": "options", "detail": ""},
    }[error_type]
    error = error_type("something went wrong", **kwargs)  # type: ignore[arg-type]
    assert "something went wrong" in str(error)
