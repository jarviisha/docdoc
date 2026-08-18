"""T015 — the layer direction, asserted mechanically (Principle X, research.md R1).

This is the check that justifies `docdoc.grounding` being its own package rather
than living inside `docdoc.extraction` as ADR-0005's text literally says. Inside
one package the dependency direction is unenforceable: `import-linter` cannot
express "grounding imports extraction but not the reverse" when both are the same
layer. If these assertions were removed, the package split would be decoration.

The `import-linter` contracts in pyproject.toml carry the same rules. This file
duplicates them on purpose: the contracts are the gate, and an AST scan is what
makes a violation legible in a test failure rather than in linter output.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "docdoc"
GROUNDING = SRC / "grounding"
EXTRACTION = SRC / "extraction"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def python_files(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts)


class TestDirection:
    def test_extraction_never_imports_grounding(self) -> None:
        for path in python_files(EXTRACTION):
            offenders = {m for m in imported_modules(path) if m.startswith("docdoc.grounding")}
            assert not offenders, f"{path.name} imports upward: {offenders}"

    def test_ingest_and_kernel_never_import_grounding(self) -> None:
        for package in ("ingest", "kernel"):
            for path in python_files(SRC / package):
                offenders = {m for m in imported_modules(path) if m.startswith("docdoc.grounding")}
                assert not offenders, f"{package}/{path.name} imports upward: {offenders}"

    def test_grounding_may_import_downward(self) -> None:
        """The direction that must remain *allowed* -- a contract too strict is also wrong."""
        seen = set()
        for path in python_files(GROUNDING):
            seen.update(
                m
                for m in imported_modules(path)
                if m.startswith(("docdoc.kernel", "docdoc.extraction"))
            )
        assert seen, "grounding is expected to build on the layers below it"


class TestNoNetworkReach:
    """FR-048 / SC-021 — this milestone's headline property, as a test.

    `pyproject.toml` carries the same rule as a forbidden-imports contract, which
    is the build gate. This states it where a reader looking for the guarantee
    would look for it.

    **The claim is about docdoc's own code, and the distinction is not pedantry.**
    Importing `docdoc.grounding` does put `socket` in `sys.modules` — but so does
    importing `docdoc.kernel` alone, because pydantic reaches `email.utils` while
    building models, and `email.utils` imports `socket`. That has been true since
    Milestone 1 and the kernel-purity test permits it.

    So the property that is real, checkable, and worth having is: **no module in
    this layer imports anything that can open a connection.** Asserting the
    transitive `sys.modules` set instead would be asserting something about
    pydantic, would fail for a reason unrelated to grounding, and would tempt
    someone to "fix" it by weakening the rule.
    """

    FORBIDDEN = (
        "socket",
        "urllib",
        "http",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "boto3",
        "azure",
        "fastapi",
        "sqlalchemy",
    )

    @pytest.mark.parametrize("path", python_files(GROUNDING), ids=lambda p: p.name)
    def test_no_module_can_reach_a_network_or_a_provider(self, path: Path) -> None:
        for module in imported_modules(path):
            root = module.split(".")[0]
            assert root not in self.FORBIDDEN, (
                f"{path.name} imports {module!r}. Grounding is deterministic and offline; "
                "an import that can reach a network breaks the property that lets the "
                "whole suite run for every contributor with nothing skipped."
            )
