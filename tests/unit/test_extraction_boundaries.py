"""T008 — the extraction layer's boundary, enforced by a test rather than convention.

Principle XII requires dependency boundaries to be machine-checked, and Principle
IV requires provider SDK types to stay inside an adapter. This is an AST scan, so
it catches an import anywhere in a module including inside a function;
``lint-imports`` runs the same contracts over the *transitive* graph in CI.
Neither subsumes the other, which is why both exist.

The task list originally said this test should "fail informatively while the
layer is still empty". It does not: a deliberately red test through two phases
is a test that gets disabled, and a disabled test protects nothing. What it does
instead is refuse to pass *vacuously* -- if the scan finds no modules to scan, it
fails, which is the real hazard a boundary test has.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import docdoc.extraction

EXTRACTION_DIR = pathlib.Path(docdoc.extraction.__file__).parent
ADAPTER_DIR = EXTRACTION_DIR / "adapters"

#: Layers above extraction in the constitution's order. None exists yet; naming
#: them now means this fails the moment one is imported downward.
HIGHER_LAYERS = frozenset({"pipeline", "api"})

PROVIDER_MODULES = frozenset(
    {"anthropic", "openai", "httpx", "requests", "boto3", "azure", "pymupdf", "fitz"}
)


def _modules() -> list[pathlib.Path]:
    return sorted(p for p in EXTRACTION_DIR.rglob("*.py"))


def _imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_scan_is_not_vacuous() -> None:
    """A boundary test that scans nothing passes for the wrong reason."""
    modules = _modules()
    assert len(modules) >= 10, f"expected the extraction layer to have modules, found {modules}"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_upward_import(path: pathlib.Path) -> None:
    """Extraction may import ingest and kernel. Never the other direction."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            module = node.names[0].name
        if module and module.startswith("docdoc."):
            layer = module.split(".")[1]
            assert layer not in HIGHER_LAYERS, (
                f"{path.name} imports {module}, which is above the extraction layer"
            )


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_provider_sdks_stay_inside_adapters(path: pathlib.Path) -> None:
    """Principle IV as a directory boundary, not a convention."""
    leaked = _imported_roots(path) & PROVIDER_MODULES
    if ADAPTER_DIR in path.parents:
        return
    assert not leaked, (
        f"{path.relative_to(EXTRACTION_DIR)} imports {sorted(leaked)}; provider SDKs are "
        "permitted only under docdoc/extraction/adapters/"
    )


def test_extraction_imports_exactly_two_names_from_ingest() -> None:
    """research.md R9 -- the coupling is deliberate and bounded.

    Extraction depends on ingest for ``ProviderError`` and ``TransportSettings``
    and nothing else. This pins the bound, so a third name arriving is a review
    decision rather than a drift.
    """
    imported: set[str] = set()
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("docdoc.ingest"):
                imported.update(alias.name for alias in node.names)
    assert imported <= {"ProviderError", "TransportSettings"}, (
        f"extraction imports {sorted(imported)} from ingest; research.md R9 bounds this to "
        "ProviderError and TransportSettings"
    )
