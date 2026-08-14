"""T010 — content-addressed identity (FR-015..FR-018, ADR-0002, research.md R3/R4).

T041 adds the adversarial collision cases at the bottom of this module.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import ClassVar

import pytest

from docdoc.kernel import (
    IdentityError,
    blob_id_for,
    canonical_json,
    document_id_for,
    options_hash_for,
)

SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class TestCanonicalJson:
    def test_key_order_does_not_affect_output(self) -> None:
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})

    def test_nested_key_order_does_not_affect_output(self) -> None:
        assert canonical_json({"x": {"a": 1, "b": 2}}) == canonical_json({"x": {"b": 2, "a": 1}})

    def test_output_has_no_insignificant_whitespace(self) -> None:
        assert canonical_json({"a": 1, "b": 2}) == b'{"a":1,"b":2}'

    def test_non_ascii_is_preserved_not_escaped(self) -> None:
        """Vietnamese text is a first-class case, not an edge case."""
        assert canonical_json({"vendor": "Công ty"}) == '{"vendor":"Công ty"}'.encode()

    def test_list_order_is_significant(self) -> None:
        assert canonical_json({"a": [1, 2]}) != canonical_json({"a": [2, 1]})

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_floats_are_rejected(self, bad: float) -> None:
        """These break both JSON interop and hash stability (research.md R3)."""
        with pytest.raises(IdentityError):
            canonical_json({"threshold": bad})

    def test_non_string_keys_are_rejected(self) -> None:
        with pytest.raises(IdentityError):
            canonical_json({1: "one"})

    def test_non_json_values_are_rejected(self) -> None:
        with pytest.raises(IdentityError) as excinfo:
            canonical_json({"when": object()})
        assert "when" in excinfo.value.field

    def test_error_names_the_offending_nested_path(self) -> None:
        with pytest.raises(IdentityError) as excinfo:
            canonical_json({"outer": {"inner": math.nan}})
        assert "inner" in excinfo.value.field


class TestBlobId:
    def test_identical_bytes_yield_identical_identity(self) -> None:
        """FR-015 — the whole point of content addressing."""
        assert blob_id_for(b"hello") == blob_id_for(b"hello")

    def test_different_bytes_yield_different_identity(self) -> None:
        assert blob_id_for(b"hello") != blob_id_for(b"hellp")

    def test_format_is_prefixed_lowercase_hex(self) -> None:
        assert SHA256_ID.match(blob_id_for(b"hello"))

    def test_matches_plain_sha256(self) -> None:
        assert blob_id_for(b"hello") == "sha256:" + hashlib.sha256(b"hello").hexdigest()

    def test_empty_input_is_valid(self) -> None:
        assert SHA256_ID.match(blob_id_for(b""))


class TestOptionsHash:
    def test_key_order_independence(self) -> None:
        """SC-004 — equivalent options must always hash equal."""
        assert options_hash_for({"dpi": 300, "lang": "vi"}) == options_hash_for(
            {"lang": "vi", "dpi": 300}
        )

    def test_empty_options_are_valid(self) -> None:
        assert SHA256_ID.match(options_hash_for({}))

    def test_different_values_hash_differently(self) -> None:
        assert options_hash_for({"dpi": 300}) != options_hash_for({"dpi": 600})

    def test_type_changes_are_visible(self) -> None:
        """1 and "1" must not collide."""
        assert options_hash_for({"dpi": 1}) != options_hash_for({"dpi": "1"})


class TestDocumentId:
    BASE: ClassVar[dict[str, str]] = {
        "blob_id": "sha256:" + "a" * 64,
        "parser_id": "pdf_text",
        "parser_version": "1.0.0",
        "options_hash": "sha256:" + "b" * 64,
    }

    def test_same_inputs_yield_same_identity(self) -> None:
        assert document_id_for(**self.BASE) == document_id_for(**self.BASE)

    def test_format_is_prefixed_lowercase_hex(self) -> None:
        assert SHA256_ID.match(document_id_for(**self.BASE))

    @pytest.mark.parametrize("field", ["blob_id", "parser_id", "parser_version", "options_hash"])
    def test_every_input_affects_the_result(self, field: str) -> None:
        """FR-016 — any difference in producing configuration must be visible."""
        changed = {**self.BASE, field: self.BASE[field] + "x"}
        assert document_id_for(**changed) != document_id_for(**self.BASE)

    def test_document_id_differs_from_blob_id(self) -> None:
        """ADR-0002 — the two levels must never be confusable."""
        assert document_id_for(**self.BASE) != self.BASE["blob_id"]


class TestConcatenationCollisions:
    """T041 — the named-field encoding must resist the ambiguity that plain
    concatenation admits (research.md R4).

    Concatenating fields makes ("pdf", "1.0") and ("pdf1", ".0") indistinguishable.
    """

    def test_parser_id_and_version_boundary_is_unambiguous(self) -> None:
        a = document_id_for(
            blob_id="sha256:" + "a" * 64,
            parser_id="pdf",
            parser_version="1.0",
            options_hash="sha256:" + "b" * 64,
        )
        b = document_id_for(
            blob_id="sha256:" + "a" * 64,
            parser_id="pdf1",
            parser_version=".0",
            options_hash="sha256:" + "b" * 64,
        )
        assert a != b

    def test_options_key_and_value_boundary_is_unambiguous(self) -> None:
        assert options_hash_for({"ab": "c"}) != options_hash_for({"a": "bc"})

    def test_adjacent_option_keys_do_not_merge(self) -> None:
        assert options_hash_for({"a": "1", "b": "2"}) != options_hash_for({"a": "1b2"})
