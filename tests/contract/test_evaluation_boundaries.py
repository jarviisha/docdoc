"""T022 — recording is not part of evaluation, and that is a check (FR-003, FR-058, SC-024).

FR-003 says "recording MUST NOT be part of evaluation". Inside one package that
sentence is a naming convention: nothing stops a scorer from importing a
recorder, and the day something does, the property that makes this feature usable
-- scoring a checkout with no credentials and no network -- is gone, silently, for
whoever installs next.

As two layers it is a build failure. ``docdoc.recording`` sits **above**
``docdoc.evaluation`` in the ``import-linter`` layers contract because the data
flows that way: the recorder produces a ``PredictionSet`` and the scorer consumes
one. The ordering follows the data, and the ordering is what makes
``evaluation -> recording`` fail.

Three checks, and none replaces another:

1. An **AST scan** of both packages, which sees imports inside functions -- where
   somebody puts one precisely because it felt like it did not count.
2. ``lint-imports`` itself, over the **transitive** graph, which sees the import
   that arrives three modules away.
3. The **direction downward**: no lower layer may import either new package.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

import docdoc.evaluation
import docdoc.recording

EVALUATION_DIR = pathlib.Path(docdoc.evaluation.__file__).parent
RECORDING_DIR = pathlib.Path(docdoc.recording.__file__).parent
SRC_ROOT = EVALUATION_DIR.parent
REPO_ROOT = SRC_ROOT.parents[1]

#: Every layer at or below evaluation. None of them may import upward.
LOWER_LAYERS = ("kernel", "ingest", "extraction", "grounding", "validation")

#: What FR-007 forbids the scorer from reaching, mirroring the pyproject contract.
FORBIDDEN_IN_EVALUATION = frozenset(
    {
        "socket",
        "urllib",
        "http",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "google",
        "boto3",
        "azure",
        "fastapi",
        "sqlalchemy",
    }
)


def _modules(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in directory.rglob("*.py"))


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module name imported anywhere in the file, function scope included."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_the_scan_is_not_vacuous() -> None:
    """A boundary test that scans nothing passes for the wrong reason."""
    assert len(_modules(EVALUATION_DIR)) >= 10, "expected the evaluation package to have modules"
    assert _modules(RECORDING_DIR), "expected the recording package to have modules"


@pytest.mark.parametrize("path", _modules(EVALUATION_DIR), ids=lambda p: p.name)
def test_evaluation_never_imports_recording(path: pathlib.Path) -> None:
    """The headline. Scoring must not be able to reach a provider (FR-003)."""
    offenders = {name for name in _imported_modules(path) if name.startswith("docdoc.recording")}
    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}. Producing a prediction set needs "
        "a provider; scoring one must not, and this import is what would make a "
        "contributor's credential-free run impossible"
    )


@pytest.mark.parametrize("path", _modules(EVALUATION_DIR), ids=lambda p: p.name)
def test_evaluation_reaches_no_network_and_no_provider(path: pathlib.Path) -> None:
    """FR-007, scanned where the graph cannot see: inside a function body."""
    roots = {name.split(".")[0] for name in _imported_modules(path)}
    leaked = roots & FORBIDDEN_IN_EVALUATION
    assert not leaked, (
        f"{path.relative_to(EVALUATION_DIR)} imports {sorted(leaked)}; the layer the "
        "quality gates read must not be able to reach a network, or its metrics are "
        "not reproducible"
    )


@pytest.mark.parametrize("path", _modules(EVALUATION_DIR), ids=lambda p: p.name)
def test_extraction_is_imported_from_submodules_never_the_package(path: pathlib.Path) -> None:
    """The mistake that broke this contract the first time it was written.

    ``from docdoc.extraction import ExtractionResult`` executes
    ``docdoc/extraction/__init__.py``, which reaches ``adapter_registry ->
    adapters.gemini -> google.genai``. import-linter follows that, and a provider
    SDK lands in the scorer's graph. Taking the submodule does not.

    The names imported are identical either way, which is precisely why this
    checks the *form* of the import rather than what it brought back.
    """
    offenders = [
        name
        for name in _imported_modules(path)
        if name in {"docdoc.extraction", "docdoc.grounding", "docdoc.validation"}
    ]
    assert not offenders, (
        f"{path.name} imports the package {offenders} rather than its submodules; "
        "that pulls the adapter registry, and through it a provider SDK, into this "
        "layer's import graph"
    )


@pytest.mark.parametrize("layer", LOWER_LAYERS)
def test_no_lower_layer_imports_the_new_packages(layer: str) -> None:
    """The other direction. A lower layer importing upward inverts the whole chain."""
    directory = SRC_ROOT / layer
    offenders: list[str] = []
    for path in _modules(directory):
        for name in _imported_modules(path):
            if name.startswith(("docdoc.evaluation", "docdoc.recording")):
                offenders.append(f"{path.relative_to(SRC_ROOT)} -> {name}")
    assert not offenders, f"{layer} imports upward: {offenders}"


def test_recording_may_import_evaluation() -> None:
    """The permitted direction, asserted so the layering is not merely absent.

    A contract that only forbids things is satisfied by two packages that never
    speak. The recorder genuinely does build a ``PredictionSet``, which is the
    dependency that fixes the order.
    """
    imported: set[str] = set()
    for path in _modules(RECORDING_DIR):
        imported |= {n for n in _imported_modules(path) if n.startswith("docdoc.evaluation")}

    assert imported, (
        "docdoc.recording imports nothing from docdoc.evaluation, so the layer "
        "ordering that makes FR-003 a build failure rests on nothing"
    )


def test_lint_imports_passes() -> None:
    """The transitive check, run rather than trusted.

    The AST scans above see one file at a time. This sees the import that arrives
    through three intermediaries, which is the shape the FR-007 contract actually
    caught in practice.
    """
    executable = pathlib.Path(sys.executable).with_name("lint-imports")
    if not executable.exists():  # pragma: no cover - depends on how dev deps landed
        pytest.skip(f"lint-imports is not installed next to {sys.executable}")

    result = subprocess.run(
        [str(executable)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"lint-imports failed:\n{result.stdout}\n{result.stderr}"
    assert "docdoc layers" in result.stdout, (
        "lint-imports ran but reported no layers contract; it may have found no "
        "configuration, in which case this test is checking nothing"
    )
    assert "0 broken" in result.stdout, result.stdout
