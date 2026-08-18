"""T094 — the engine knows no document types and no providers (SC-014, SC-013).

Principle VI's whole content is that document-type knowledge lives in schema and
prompt **data**, never in a code path. Adding a document type must be adding two
files. `InvoiceService` must be impossible to write without the build noticing.

SC-014 requires that "verified by an automated check that fails the build". Until
this file existed, it was not: `test_no_provider_names.py` is Milestone 2's, scans
`src/docdoc/ingest`, and contains no mention of extraction. The only coverage was
`test_a_second_document_type_needs_no_engine_change`, which extracts `receipt@1`
successfully — a behavioural test proving a schema works, which says nothing about
whether code branches on one. A passing extraction and an absent conditional are
different claims.

Two rules, checked mechanically because both erode one convenient special case at
a time:

1. No module under `src/docdoc/extraction/` may name a registered document type.
2. No module outside `adapters/` may name a provider, a model, or a provider SDK.

The forbidden document-type names are **derived from `schemas/`**, not hardcoded,
so registering `purchase_order@1` tomorrow extends this check by itself. A list
that has to be maintained by hand is a list that goes stale exactly when a new
document type makes it matter.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import docdoc.extraction

EXTRACTION_ROOT = pathlib.Path(docdoc.extraction.__file__).parent
SCHEMAS = pathlib.Path("schemas")
EXAMPLES = pathlib.Path("examples")

#: The adapters are the implementations, and the registry's job is to know which
#: adapters exist — the same exception the ingest layer's registry gets, and for
#: the same reason. Everything else is held to the rule.
ALLOWED_TO_NAME_A_PROVIDER = {"adapter_registry.py"}

#: Names that mean "a provider, a model, or its SDK".
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
    """Every registered schema's name, read from `schemas/` rather than hardcoded."""
    names = set()
    for path in sorted(SCHEMAS.glob("*.json")):
        names.add(str(json.loads(path.read_text(encoding="utf-8"))["name"]))
    return tuple(sorted(names))


def _library_modules() -> list[pathlib.Path]:
    return sorted(EXTRACTION_ROOT.rglob("*.py"))


def _executable_code(path: pathlib.Path) -> str:
    """The module's code with its prose removed.

    The rule is about *code*. A docstring naming `invoice@1` as an example is
    documentation, and a rule that forbade that would stand in the way of
    explaining itself — this module's own docstring names three document types.
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
    # Comments are prose too, and this layer explains itself in them heavily.
    return "\n".join(
        line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        for line in stripped.splitlines()
    )


def test_the_scan_is_not_vacuous() -> None:
    """A structural check that scans nothing passes for the wrong reason."""
    assert len(_library_modules()) >= 12
    assert len(_document_type_names()) >= 2, (
        "at least two document types must be registered, or 'no code path names one' "
        "is trivially true"
    )


@pytest.mark.parametrize("path", _library_modules(), ids=lambda p: p.name)
def test_no_module_names_a_document_type(path: pathlib.Path) -> None:
    """SC-014 — the check the spec asked for and the milestone did not have.

    `if schema.name == "invoice"` is the shape this forbids. So is a dict keyed by
    document type, and so is a constant holding one.
    """
    code = _executable_code(path)
    for name in _document_type_names():
        assert name not in code, (
            f"{path.name} names the document type {name!r} in code. Document-type "
            "knowledge belongs in schemas/ and its prompts, never in a code path "
            "(Principle VI, SC-014). If this is a docstring or comment, it is not "
            "matched here — so this is real code"
        )


@pytest.mark.parametrize("path", _library_modules(), ids=lambda p: p.name)
def test_no_module_outside_adapters_names_a_provider(path: pathlib.Path) -> None:
    """SC-013's other half, which was verified for ingest and for nothing else."""
    if path.parent.name == "adapters" or path.name in ALLOWED_TO_NAME_A_PROVIDER:
        return
    code = _executable_code(path).lower()
    for name in PROVIDER_NAMES:
        assert name not in code, (
            f"{path.name} names {name!r} in code. Which provider answers is "
            "configuration; only the adapters and the registry that wires them up "
            "may know a provider by name (Principle IV, SC-013)"
        )


def test_the_registry_names_adapter_ids_and_imports_no_sdk() -> None:
    """Bounds the one exception rather than trusting it.

    `adapter_registry.py` must know that an adapter called `gemini` exists — that
    is its job, and the ingest registry does the same with `pdf-text` and
    `azure-di`. What it must not do is reach the SDK, which would put a provider
    dependency in the selection path.
    """
    path = EXTRACTION_ROOT / "adapter_registry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    sdk_roots = {"google", "anthropic", "openai", "azure", "httpx", "requests", "boto3"}
    assert not roots & sdk_roots, (
        f"the adapter registry imports {sorted(roots & sdk_roots)}; selection must not "
        "depend on a provider SDK. The registry may know that an adapter named 'gemini' "
        "exists; it may not reach the library behind it"
    )


def test_the_allowlist_stays_small() -> None:
    """If a second module starts naming providers, that becomes a reviewed change
    rather than a habit."""
    assert len(ALLOWED_TO_NAME_A_PROVIDER) == 1


def test_the_public_surface_exposes_no_concrete_adapter() -> None:
    """A provider name in `__all__` would make the import a caller writes name one."""
    for name in docdoc.extraction.__all__:
        assert "gemini" not in name.lower()
        assert "anthropic" not in name.lower()


def test_the_documented_example_names_no_document_type_in_its_code() -> None:
    """SC-020's example is what a new contributor copies.

    It necessarily *builds* an invoice schema — that is the demonstration — so the
    rule here is narrower than for the library: the example may define a schema as
    data, and must not branch on one.
    """
    source = (EXAMPLES / "extract_invoice.py").read_text(encoding="utf-8")
    for forbidden in ("schema.name ==", "identity ==", 'if "invoice"'):
        assert forbidden not in source, (
            f"the example branches on a document type ({forbidden!r}), which models "
            "exactly what the engine forbids"
        )


def test_the_check_can_actually_fail() -> None:
    """Guards the guard.

    A structural check whose matcher is subtly wrong passes on everything, and is
    the most comfortable kind of dead test. This feeds it a module that plainly
    violates both rules and confirms it objects.
    """
    import tempfile

    offending = (
        '"""A docstring naming invoice and gemini, which must NOT trip the check."""\n'
        "def route(schema):\n"
        '    if schema.name == "invoice":\n'
        '        return "gemini"\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "offender.py"
        path.write_text(offending, encoding="utf-8")
        code = _executable_code(path)

        assert "invoice" in code, "the document-type matcher would have missed a real branch"
        assert "gemini" in code.lower(), "the provider matcher would have missed a real name"
        assert "A docstring naming" not in code, "prose must be stripped, or every module trips"
