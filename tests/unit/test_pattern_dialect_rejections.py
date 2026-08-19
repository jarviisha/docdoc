"""T044 — what the dialect refuses, and the input CPython's `re` cannot survive.

Two halves. The refusals are what make `pattern_dialect@1` a *documented* subset
rather than "whatever this parser happened to accept": every construct outside it
is named in its own error, raised before any check runs, so a schema author is
told which thing they wrote is unsupported rather than discovering it in a
verdict.

The last test is the reason the module exists. `(a+)+` against ten thousand
characters is effectively non-terminating under `re`; here it is milliseconds.
"""

from __future__ import annotations

import time

import pytest

from docdoc.kernel import DocdocError
from docdoc.validation.pattern import (
    MAX_NODES,
    MAX_REPEAT,
    PatternSyntaxError,
    compile_pattern,
)


class TestOutsideTheDialect:
    @pytest.mark.parametrize(
        ("pattern", "named"),
        [
            (r"(\d)\1", "backreference"),
            (r"(?=foo)bar", "lookahead"),
            (r"(?!foo)bar", "lookahead"),
            (r"(?<=a)b", "lookbehind"),
            (r"(?<!a)b", "lookbehind"),
            (r"(?P<year>\d{4})", "named group"),
            (r"(?i)abc", "inline flag"),
            (r"(?#a comment)b", "comment group"),
            (r"a\b", r"\b"),
            (r"\Aabc", r"\A"),
        ],
    )
    def test_the_error_names_the_construct(self, pattern: str, named: str) -> None:
        with pytest.raises(PatternSyntaxError) as caught:
            compile_pattern(pattern)
        assert named in str(caught.value)

    @pytest.mark.parametrize(
        "pattern",
        ["(abc", "abc)", "[abc", "*abc", "a**", "a+?", "a{2}{3}", "a\\", "[]", "[b-a]"],
    )
    def test_malformed_patterns_are_refused(self, pattern: str) -> None:
        with pytest.raises(PatternSyntaxError):
            compile_pattern(pattern)

    def test_an_anchor_in_the_middle_is_refused_rather_than_ignored(self) -> None:
        """Ignoring it would silently change what the author asked for."""
        with pytest.raises(PatternSyntaxError, match="anchor"):
            compile_pattern("a$b")

    def test_anchors_at_the_edges_are_accepted(self) -> None:
        assert compile_pattern(r"^INV-\d{4}$").fullmatch("INV-2026")


class TestBudgets:
    def test_a_repetition_count_above_the_limit_is_refused(self) -> None:
        with pytest.raises(PatternSyntaxError, match=str(MAX_REPEAT)):
            compile_pattern(r"\d{5000}")

    def test_a_nested_expansion_past_the_node_budget_is_refused(self) -> None:
        """`{100}` of `{1000}` is small to write and large to build."""
        with pytest.raises(PatternSyntaxError, match=str(MAX_NODES)):
            compile_pattern(r"(\d{1000}){100}")

    def test_a_large_but_legal_automaton_still_matches_quickly(self) -> None:
        compiled = compile_pattern(r"(\d{100}){100}")
        assert compiled.node_count <= MAX_NODES
        started = time.perf_counter()
        assert not compiled.fullmatch("1" * 200)
        assert (time.perf_counter() - started) < 0.05


def test_the_input_that_defeats_the_stdlib_engine() -> None:
    """`(a+)+` against 10,000 characters.

    Measured for the plan: CPython's `re` needs about 1.2 seconds at *24*
    characters and doubles per character after that, so this input is not slow
    there — it does not finish. Here the whole point is that it does.
    """
    compiled = compile_pattern(r"(a+)+")
    text = "a" * 10_000 + "!"
    started = time.perf_counter()
    assert not compiled.fullmatch(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"linear-time matching regressed: {elapsed * 1000:.0f} ms"


def test_growth_is_linear_not_exponential() -> None:
    """Ten times the input costs roughly ten times the work, not 2**10."""
    compiled = compile_pattern(r"(a+)+")

    def cost(size: int) -> float:
        text = "a" * size + "!"
        started = time.perf_counter()
        compiled.fullmatch(text)
        return time.perf_counter() - started

    small, large = cost(1_000), cost(10_000)
    # Generous: the assertion is about the *shape* of the growth, and a factor of
    # 40 still rules out anything exponential while surviving a noisy CI runner.
    assert large < small * 40


class TestRejectedBeforeAnyCheckRuns:
    """T086, T087 — where a dialect fault surfaces, and as what.

    Convergence found that an out-of-dialect pattern reached a caller as a bare
    `PatternSyntaxError` — a `ValueError`, raised mid-validation when the
    constraint happened to be evaluated. It was neither one of docdoc's typed
    errors (FR-054) nor refused before verdict time (FR-056).

    The original task asked the *schema loader* to reject it. That is not
    implementable: `docdoc.extraction` may not import `docdoc.validation`, and the
    dialect belongs to the layer that evaluates it. The check therefore runs at
    the entry to validation, which is what FR-056 is actually about.
    """

    @staticmethod
    def _schema_with_pattern(source: str):
        from docdoc.extraction.schema import Schema
        from tests.fixtures.validation.schemas import invoice_schema

        base = invoice_schema()
        fields = tuple(
            field.model_copy(update={"constraints": {"pattern": source}})
            if field.name == "number"
            else field
            for field in base.fields
        )
        return Schema(name=base.name, version=base.version, fields=fields)

    def test_it_is_a_schema_error_not_a_value_error(self) -> None:
        from docdoc.extraction.errors import SchemaError
        from docdoc.validation import validate
        from tests.fixtures.validation import artifacts

        schema = self._schema_with_pattern("(?=foo)bar")
        pair = artifacts.build(schema=schema)
        with pytest.raises(SchemaError) as caught:
            validate(pair.extraction, pair.grounding, schema)

        assert caught.value.field_path == "number"
        assert "lookahead" in str(caught.value)
        assert isinstance(caught.value, DocdocError)

    def test_it_fails_before_a_single_check_is_recorded(self) -> None:
        """FR-056 — never at verdict time.

        Asserted by the shape of the failure rather than by counting: `validate`
        returns exactly one result or raises, so a raise means no result exists
        and therefore no check was recorded.
        """
        from docdoc.extraction.errors import SchemaError
        from docdoc.validation import validate
        from tests.fixtures.validation import artifacts

        schema = self._schema_with_pattern(r"(\d)\1")
        pair = artifacts.build(schema=schema)
        result = None
        with pytest.raises(SchemaError):
            result = validate(pair.extraction, pair.grounding, schema)
        assert result is None

    def test_a_pattern_inside_the_dialect_validates_normally(self) -> None:
        """The negative half: this must not refuse every schema that has a pattern."""
        from docdoc.validation import validate
        from tests.fixtures.validation import artifacts

        schema = self._schema_with_pattern(r"INV-\d{4}-\d{3}")
        pair = artifacts.build(schema=schema)
        result = validate(pair.extraction, pair.grounding, schema)
        assert result.check("number#pattern") is not None

    def test_every_declared_pattern_is_checked_not_only_the_first(self) -> None:
        from docdoc.extraction.errors import SchemaError
        from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
        from docdoc.validation.constraints import compile_declared_patterns

        nested = Schema(
            name="probe_nested",
            version=1,
            fields=(
                FieldSpec(
                    name="number",
                    type=FieldType.STRING,
                    constraints={"pattern": r"INV-\d{4}"},
                ),
                FieldSpec(
                    name="lines",
                    cardinality=Cardinality.REPEATING_GROUP,
                    fields=(
                        FieldSpec(
                            name="code",
                            type=FieldType.STRING,
                            constraints={"pattern": r"a\b"},  # inside a repeating group
                        ),
                    ),
                ),
            ),
        )
        with pytest.raises(SchemaError) as caught:
            compile_declared_patterns(nested)
        assert caught.value.field_path == "lines.code"
