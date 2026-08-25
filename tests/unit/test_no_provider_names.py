"""T051 — docdoc's own code chooses parsers by capability, never by name (SC-011).

This is the boundary that keeps the project from becoming a vendor wrapper, and
it is the kind of rule that erodes one convenient import at a time. So it is
checked mechanically rather than left to review.

The rule: outside ``docdoc/ingest/parsers/`` and the registry that wires them up,
no module may name a concrete parser -- not its class, not its id.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import docdoc.ingest

SOURCE_ROOT = pathlib.Path(docdoc.ingest.__file__).parent.parent
EXAMPLES = SOURCE_ROOT.parent.parent / "examples"

#: The registry is the one module whose job is to know these names, and the
#: adapters are the implementations themselves.
ALLOWED = {"registry.py", "pdf_text.py", "azure_di.py", "gcv.py"}

#: ``assess.py`` may name the native reader in an *error message*, and only
#: there. The coupling is inherent rather than incidental: the text-layer
#: question is "what can the native reader extract from this file?", so an
#: install without that reader cannot answer it, and saying which parser is
#: missing is the whole value of the error (research.md R4, FR-012). It may
#: still not import a parser class, and it performs no selection.
ALLOWED_TO_NAME_IN_MESSAGES = {"assess.py"}

CONCRETE_PARSER_NAMES = (
    "PdfTextParser",
    "AzureDocumentIntelligenceParser",
    "GoogleCloudVisionParser",
)
PARSER_IDS = ("pdf-text", "azure-di", "gcv")


def library_modules() -> list[pathlib.Path]:
    return sorted(
        path for path in (SOURCE_ROOT / "ingest").rglob("*.py") if path.name not in ALLOWED
    )


def executable_code(path: pathlib.Path) -> str:
    """The module's code with its prose removed.

    The rule under test is about *code*. A docstring naming a parser as an
    example is documentation, and a rule that forbade that would stand in the
    way of explaining itself.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            first.value.value = ""
    return ast.unparse(tree)


@pytest.mark.parametrize("path", library_modules(), ids=lambda p: p.name)
def test_no_module_imports_a_concrete_parser(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    offenders = imported & set(CONCRETE_PARSER_NAMES)
    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}; ask for a capability and let the "
        "registry choose (Principle IV, FR-015)"
    )


@pytest.mark.parametrize("path", library_modules(), ids=lambda p: p.name)
def test_no_module_selects_a_parser_by_id(path: pathlib.Path) -> None:
    if path.name in ALLOWED_TO_NAME_IN_MESSAGES:
        pytest.skip(f"{path.name} may name a parser in an error message only")

    code_only = executable_code(path)

    for parser_id in PARSER_IDS:
        assert f"'{parser_id}'" not in code_only, (
            f"{path.name} names the parser {parser_id!r} in code; selection must be "
            "expressed as a capability request"
        )


def test_the_one_module_allowed_to_name_a_parser_only_does_so_in_a_message() -> None:
    """Bounds the exception rather than trusting it.

    ``assess.py`` may mention the native reader when explaining why it cannot
    answer. It may not import one, and it may not choose one.
    """
    for name in ALLOWED_TO_NAME_IN_MESSAGES:
        path = SOURCE_ROOT / "ingest" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        assert not imported & set(CONCRETE_PARSER_NAMES), (
            f"{name} imports a concrete parser class, which goes beyond naming one "
            "in an error message"
        )
        assert "select(" not in path.read_text(encoding="utf-8"), (
            f"{name} performs selection; that belongs in the registry"
        )


def test_the_example_selects_by_capability() -> None:
    """The documented example is what a new user copies, so it has to model the
    supported way of asking."""
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    source = (EXAMPLES / "parse_pdf.py").read_text(encoding="utf-8")

    assert "CapabilityRequest" in source
    for name in (*CONCRETE_PARSER_NAMES, *PARSER_IDS):
        assert name not in source


def test_the_public_surface_exposes_no_concrete_parser() -> None:
    for name in CONCRETE_PARSER_NAMES:
        assert name not in docdoc.ingest.__all__


def test_the_allowlist_itself_stays_small() -> None:
    """If a third adapter appears, this fails and the exception list becomes a
    reviewed change rather than a habit."""
    adapters = {path.name for path in (SOURCE_ROOT / "ingest" / "parsers").glob("*.py")} - {
        "__init__.py"
    }

    assert adapters <= ALLOWED
