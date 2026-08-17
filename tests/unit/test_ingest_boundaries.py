"""T025 — the layer boundary, enforced by a test rather than by convention.

Constitution Principle X requires the dependency direction to be machine-checked,
and Principle IV requires provider SDK types to stay inside an adapter. SC-008
makes a violation a build failure.

This is an AST scan, so it catches an import anywhere in a module including
inside a function. ``lint-imports`` runs the same contracts over the *transitive*
graph in CI; neither check subsumes the other, which is why both exist.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import docdoc.ingest
import docdoc.kernel

INGEST_DIR = pathlib.Path(docdoc.ingest.__file__).parent
KERNEL_DIR = pathlib.Path(docdoc.kernel.__file__).parent
ADAPTER_DIR = INGEST_DIR / "parsers"

# Layers above ingest in the constitution's order. None of them exists yet;
# naming them now means this test fails the moment one is imported downward.
HIGHER_LAYERS = frozenset({"transform", "extraction", "pipeline", "api"})

# Provider SDKs and transports. Permitted only under parsers/ (Principle IV).
PROVIDER_MODULES = frozenset(
    {
        "pymupdf",
        "fitz",
        "azure",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "boto3",
        "fastapi",
        "sqlalchemy",
    }
)


def ingest_modules() -> list[pathlib.Path]:
    return sorted(path for path in INGEST_DIR.rglob("*.py"))


def imported_top_levels(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def docdoc_submodules_imported(path: pathlib.Path) -> set[str]:
    """Which ``docdoc.<x>`` packages this module imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("docdoc"):
            parts = node.module.split(".")
            if len(parts) > 1:
                found.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "docdoc" and len(parts) > 1:
                    found.add(parts[1])
    return found


@pytest.mark.parametrize("path", ingest_modules(), ids=lambda p: p.name)
def test_ingest_never_imports_a_higher_layer(path: pathlib.Path) -> None:
    """Dependencies flow downward only: ingest may know about the kernel, and
    nothing above it may be visible from here."""
    offenders = docdoc_submodules_imported(path) & HIGHER_LAYERS

    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}, which sit above ingest in the "
        "dependency order (Principle X)"
    )


@pytest.mark.parametrize("path", ingest_modules(), ids=lambda p: p.name)
def test_provider_sdks_appear_only_inside_adapters(path: pathlib.Path) -> None:
    offenders = imported_top_levels(path) & PROVIDER_MODULES

    if path.parent == ADAPTER_DIR and path.name != "__init__.py":
        return  # adapters are the one place these belong

    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}; provider SDKs belong only in "
        "docdoc/ingest/parsers/ (Principle IV)"
    )


def test_the_kernel_still_knows_nothing_about_ingest() -> None:
    """The direction has to hold from both ends.

    A kernel that imported ingest would make the whole order meaningless, and it
    is the sort of thing an editor's auto-import does silently.
    """
    for path in sorted(KERNEL_DIR.rglob("*.py")):
        assert "ingest" not in docdoc_submodules_imported(path), (
            f"kernel module {path.name} imports from ingest"
        )


def test_the_adapter_directory_is_the_only_exception_and_is_small() -> None:
    """Guards the exception itself.

    The provider-containment rule is only as good as the list of files allowed
    to break it. If a third adapter appears, this test fails and the import-linter
    contract must be updated in the same change -- which is the review the
    constitution asks for.
    """
    adapters = {path.name for path in ADAPTER_DIR.glob("*.py")} - {"__init__.py"}

    assert adapters <= {"pdf_text.py", "azure_di.py"}, (
        f"unexpected adapter modules {sorted(adapters)}; add them to the "
        "import-linter ignore list in pyproject.toml in the same change"
    )
