"""T054 — nothing in this layer asks a model anything (FR-008).

Including — and this is the case worth stating, because it is the one somebody
will propose — whether a predicted value *means* the same as the expected one.
"1,240.00" against "1240.00", "Acme Ltd" against "ACME LIMITED": an LLM judge
would resolve those, and the appeal is obvious.

It is the same failure Principle II forbids for grounding, and it is no more
acceptable when the subject is accuracy. A model judging output produced by a
model gives a number that moves when the judge is upgraded, cannot be reproduced
by whoever reads the report, and is systematically kind to the failure modes both
models share. The score would stop measuring the pipeline and start measuring the
agreement between two models.

The alternative is not "be stricter". It is a **declared, versioned comparator**
(FR-024), recorded next to every metric it affected — so leniency is a visible
decision with an identity that moves, rather than a judgement call made per
comparison by something nobody can inspect.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import docdoc.evaluation
from docdoc.evaluation import evaluate
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

EVALUATION_DIR = pathlib.Path(docdoc.evaluation.__file__).parent

#: Names that would mean a model is being reached, directly or through docdoc's
#: own adapter machinery.
MODEL_NAMES = frozenset(
    {
        "ModelAdapter",
        "EchoAdapter",
        "GeminiAdapter",
        "adapter_registry",
        "default_adapter",
        "ModelRequest",
        "ModelResponse",
        "extract",
    }
)


def _modules() -> list[pathlib.Path]:
    return sorted(EVALUATION_DIR.rglob("*.py"))


def test_the_scan_is_not_vacuous() -> None:
    assert len(_modules()) >= 10


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_module_imports_an_adapter(path: pathlib.Path) -> None:
    """The static half. An adapter that is never imported cannot be constructed."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            ("docdoc.extraction.adapter", "docdoc.extraction.prompt")
        ):
            offenders.append(node.module or "")
        elif isinstance(node, ast.ImportFrom) and node.module:
            offenders += [
                f"{node.module}.{alias.name}" for alias in node.names if alias.name in MODEL_NAMES
            ]
    assert not offenders, f"{path.name} imports model machinery: {offenders}"


def test_scoring_runs_with_every_adapter_constructor_patched_to_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behavioural half, which the AST scan cannot give.

    A module could reach an adapter through a registry lookup, a string, or a
    late import. This makes construction itself fatal and then scores anyway.
    """
    from docdoc.extraction import adapter_registry
    from docdoc.extraction.adapters import echo

    # Built first, and deliberately: recording a prediction set *does* use an
    # adapter -- that is `docdoc.recording`'s whole job. What must not use one is
    # scoring, so the trap is armed only after the inputs exist.
    golden = golden_set()
    predictions = prediction_set()
    facts = facts_for_fixtures()

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "evaluation constructed a model adapter. Scoring must not ask a model "
            "anything, including whether two values mean the same thing (FR-008)"
        )

    monkeypatch.setattr(echo.EchoAdapter, "__init__", explode)
    monkeypatch.setattr(adapter_registry, "default_adapter", explode, raising=False)

    report = evaluate(golden, predictions, facts=facts)

    assert report.metrics.micro["field_accuracy"].value is not None


def test_a_near_miss_stays_incorrect_rather_than_being_judged() -> None:
    """The concrete case. 300.00 against 350.00 is wrong, and stays wrong.

    Not "close". Not "semantically equivalent". A comparator decided it, and the
    outcome records which comparator did.
    """
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    total = next(
        outcome
        for outcome in report.outcomes
        if outcome.document_id == "near-miss" and outcome.field_path == "total"
    )

    assert str(total.kind) == "incorrect"
    assert total.expected == "350.00"
    assert total.predicted == "300.00"
    assert total.comparator_version == "exact@1", (
        "the outcome must name the rule that decided it, so a future leniency is "
        "visible in the report rather than only in the code"
    )


def test_the_package_declares_no_model_dependency() -> None:
    """Importing the scorer must not pull a provider SDK into the process."""
    import sys

    for name in ("google", "google.genai", "openai", "anthropic"):
        assert name not in sys.modules or "docdoc.evaluation" not in str(
            getattr(sys.modules[name], "__file__", "")
        ), f"{name} was imported by way of docdoc.evaluation"
