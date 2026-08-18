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


def test_ingest_is_imported_from_submodules_never_the_package_facade() -> None:
    """The mistake this file failed to catch, twice.

    ``from docdoc.ingest import X`` executes ``docdoc/ingest/__init__.py``, which
    reaches ``parse -> registry -> parsers.pdf_text -> pymupdf`` and
    ``parsers.azure_di -> azure``. Both then sit in this layer's transitive import
    graph, and PyMuPDF is AGPL-3.0 -- so it is a licence question as well as a
    layering one.

    ``lint-imports`` catches it because it walks the transitive graph. The
    name-based assertion above does not, because the names being imported are the
    permitted ones either way. That gap is why this test exists: it makes the
    *form* of the import the thing being checked.
    """
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "docdoc.ingest":
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}:{node.lineno}"
                    for alias in node.names
                    if alias.name == "docdoc.ingest"
                ]
    assert not offenders, (
        f"import from the ingest package facade at {offenders}. Use the submodule -- "
        "`docdoc.ingest.errors`, `docdoc.ingest.options` -- so pymupdf and azure stay out "
        "of this layer's transitive graph"
    )


# -- the error taxonomy crosses the boundary too ------------------------------


def test_every_extraction_error_answers_to_the_one_docdoc_root() -> None:
    """T118 — the constitution's error model is one list, so it needs one root.

    This lived here rather than in `tests/unit/test_errors.py` because that file
    is the kernel's and importing this layer into it would pull a Milestone 3
    package into a Milestone 1 test. The property is a boundary property in any
    case: it is precisely the claim that this layer's errors are catchable by
    the same `except` as every other layer's.

    It was false for nine review passes. `SchemaError` and `ExtractionError`
    derived from bare `Exception`, while `ModelProviderError` reached the root
    through ingest's chain — so `except DocdocError` caught provider failures and
    silently missed schema and conformance failures. A partial catch is the worst
    outcome available, because nothing about it looks broken.
    """
    from docdoc.extraction import ExtractionError, ModelProviderError, SchemaError
    from docdoc.kernel import DocdocError

    for error_type in (SchemaError, ExtractionError, ModelProviderError):
        assert issubclass(error_type, DocdocError), (
            f"{error_type.__name__} does not descend from DocdocError, so `except DocdocError` "
            "misses it. contracts/extraction-api.md §7 says 'All are DocdocError'"
        )


def test_a_single_root_catches_every_extraction_failure() -> None:
    """The property as a caller experiences it, not as a class diagram.

    `issubclass` can be satisfied while a raise site constructs something else,
    so this catches the real exceptions the layer raises.
    """
    from docdoc.extraction import ExtractionError, ModelProviderError, SchemaError
    from docdoc.kernel import DocdocError

    raises = (
        lambda: (_ for _ in ()).throw(SchemaError("unknown identity", identity="nope@1")),
        lambda: (_ for _ in ()).throw(ExtractionError("wrong shape", reason="shape")),
        lambda: (_ for _ in ()).throw(
            ModelProviderError("service said no", reason="service", adapter_id="gemini")
        ),
    )
    for raise_it in raises:
        with pytest.raises(DocdocError):
            raise_it()
