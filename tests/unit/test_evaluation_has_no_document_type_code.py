"""T086 — the scorer and the recorder know no document types (FR-013, Principle VI).

Extending the pattern of ``test_extraction_has_no_document_type_code.py`` to both
new packages, and the argument needs one addition, because this layer has a
second way to break the rule.

**In code**, the failure is the familiar one: ``if schema.name == "invoice"`` in a
comparator, an alignment key defaulted for line items, a metric that only applies
to a document type. Each arrives as a convenient special case and each makes
"adding a document type is adding two files" false.

**In data**, the failure is subtler and specific to this milestone. FR-013 says a
dataset spans at least two schemas — and a golden set of nothing but invoices
would reintroduce in *data* exactly the coupling Principle VI forbids in code.
Every metric in the repository would then describe invoice performance while
being reported as accuracy, and no code check anywhere would notice. So the
committed dataset is checked too.

Document-type names are **derived from ``schemas/``**, never hardcoded, so
registering ``purchase_order@1`` tomorrow extends this check by itself.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import docdoc.evaluation
import docdoc.recording

EVALUATION_ROOT = pathlib.Path(docdoc.evaluation.__file__).parent
RECORDING_ROOT = pathlib.Path(docdoc.recording.__file__).parent
SCHEMAS = pathlib.Path("schemas")
DATASET = pathlib.Path("datasets/mvp")

#: Names that mean "a provider, a model, or its SDK". Neither new package has any
#: business naming one: the scorer never reaches a model at all, and the recorder
#: takes whichever adapter it is handed.
PROVIDER_NAMES = (
    "gemini",
    "google-genai",
    "anthropic",
    "claude",
    "openai",
    "gpt-",
    "azure",
    "bedrock",
    "mistral",
    "llama",
)


def _document_type_names() -> tuple[str, ...]:
    names = set()
    for path in sorted(SCHEMAS.glob("*.json")):
        names.add(str(json.loads(path.read_text(encoding="utf-8"))["name"]))
    return tuple(sorted(names))


def _library_modules() -> list[pathlib.Path]:
    return sorted(EVALUATION_ROOT.rglob("*.py")) + sorted(RECORDING_ROOT.rglob("*.py"))


def _executable_code(path: pathlib.Path) -> str:
    """The module's code with its prose removed.

    The rule is about *code*. A docstring naming ``invoice@1`` as an example is
    documentation, and a rule that forbade that would stand in the way of
    explaining itself -- this module's own docstring names one.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    docstrings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.append(first.value.value)

    stripped = source
    for text in docstrings:
        stripped = stripped.replace(text, "")
    return "\n".join(
        line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        for line in stripped.splitlines()
    )


def test_the_scan_is_not_vacuous() -> None:
    assert len(_library_modules()) >= 12
    assert len(_document_type_names()) >= 2, (
        "at least two document types must be registered, or 'no code path names one' "
        "is trivially true"
    )


@pytest.mark.parametrize("path", _library_modules(), ids=lambda p: p.name)
def test_no_module_names_a_document_type(path: pathlib.Path) -> None:
    """A metric, comparator, or alignment rule that knew about invoices."""
    code = _executable_code(path)
    for name in _document_type_names():
        assert name not in code, (
            f"{path.name} names the document type {name!r} in code. Document-type "
            "knowledge belongs in schemas/ and in the dataset, never in a code path "
            "(Principle VI, FR-013). Docstrings and comments are not matched here, "
            "so this is real code"
        )


@pytest.mark.parametrize("path", _library_modules(), ids=lambda p: p.name)
def test_no_module_names_a_provider(path: pathlib.Path) -> None:
    """Neither package has an adapters directory, so there is no exception to make."""
    code = _executable_code(path).lower()
    for name in PROVIDER_NAMES:
        assert name not in code, (
            f"{path.name} names {name!r} in code. The scorer reaches no model at all, "
            "and the recorder uses whichever adapter it is handed (Principle IV)"
        )


def test_no_metric_is_named_after_a_document_type() -> None:
    """A ``invoice_total_accuracy`` would be Principle VI arriving through a key."""
    from docdoc.evaluation.definitions import METRICS

    for spec in METRICS:
        for name in _document_type_names():
            assert name not in spec.name, f"the metric {spec.name!r} names a document type"


def test_the_committed_dataset_spans_more_than_one_schema() -> None:
    """FR-013, checked against the data rather than against an intention.

    A golden set of nothing but invoices would make every number in this
    repository a statement about invoices, reported as a statement about
    accuracy — the coupling Principle VI forbids, relocated from code to data
    where no code check would find it.
    """
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    identities = {document["schema_identity"] for document in manifest["documents"]}

    assert len(identities) >= 2, (
        f"the committed dataset uses only {identities}. FR-013 requires at least two "
        "schemas, or the dataset is a document-type assumption wearing a metric's name"
    )


def test_the_public_tier_itself_spans_more_than_one_schema() -> None:
    """The stronger version, and the one that matters.

    A public tier of invoices with a restricted tier of receipts would satisfy the
    check above while leaving every number a contributor can actually compute
    invoice-shaped.
    """
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    public = {
        document["schema_identity"]
        for document in manifest["documents"]
        if document["tier"] == "public"
    }

    assert len(public) >= 2, f"the public tier uses only {public}"


def test_the_public_surface_names_no_document_type() -> None:
    """An export a caller writes must not name one."""
    for name in (*docdoc.evaluation.__all__, *docdoc.recording.__all__):
        for document_type in _document_type_names():
            assert document_type not in name.lower(), (
                f"the exported name {name!r} contains the document type {document_type!r}"
            )
