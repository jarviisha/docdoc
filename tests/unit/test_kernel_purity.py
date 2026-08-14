"""T006 — the kernel's dependency boundary and purity, enforced mechanically.

Constitution Principle I and FR-020/FR-021/SC-005. Two complementary checks
(research.md R9):

1. A static AST scan, which catches forbidden imports anywhere in a module,
   including inside functions, and covers the clock and randomness -- for which
   ``sys.audit`` emits no events.
2. A runtime audit hook, which catches I/O reached indirectly through an
   otherwise-allowed module.

Neither is sufficient alone.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

import docdoc.kernel

KERNEL_DIR = pathlib.Path(docdoc.kernel.__file__).parent

# The kernel's only permitted runtime dependency is pydantic (Constitution
# Principle I), plus this standard-library allowlist (plan.md Technical Context).
ALLOWED_TOP_LEVEL = frozenset(
    {
        "pydantic",
        "bisect",
        "hashlib",
        "json",
        "math",
        "typing",
        "dataclasses",
        "enum",
        "re",
        "unicodedata",
        "collections",
        "__future__",
        "docdoc",
    }
)

# Named explicitly so a failure message says *why* the import is forbidden,
# rather than only that it is missing from the allowlist.
FORBIDDEN_WITH_REASON = {
    "time": "the kernel must not read the clock (FR-020)",
    "datetime": "the kernel must not read the clock (FR-020)",
    "random": "the kernel must be deterministic (FR-019)",
    "secrets": "the kernel must be deterministic (FR-019)",
    "uuid": "identity is content-derived, never random (ADR-0002)",
    "os": "the kernel performs no I/O (FR-020)",
    "io": "the kernel performs no I/O (FR-020)",
    "pathlib": "the kernel performs no I/O (FR-020)",
    "socket": "the kernel performs no network access (FR-020)",
    "subprocess": "the kernel spawns no processes (FR-020)",
    "urllib": "the kernel performs no network access (FR-020)",
    "requests": "the kernel performs no network access (FR-020)",
    "httpx": "the kernel performs no network access (FR-020)",
    "sqlalchemy": "the kernel does not depend on storage (Principle X)",
    "fastapi": "the kernel does not depend on transport (Principle X)",
    "rapidfuzz": "fuzzy matching lives in the extraction layer (ADR-0005)",
}


def kernel_modules() -> list[pathlib.Path]:
    return sorted(KERNEL_DIR.rglob("*.py"))


def imported_top_level_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays inside the kernel
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_kernel_has_modules() -> None:
    """Guard against the scan passing vacuously on an empty package."""
    assert len(kernel_modules()) > 5


@pytest.mark.parametrize("path", kernel_modules(), ids=lambda p: p.name)
def test_kernel_module_imports_are_allowlisted(path: pathlib.Path) -> None:
    for name in imported_top_level_names(path):
        reason = FORBIDDEN_WITH_REASON.get(name)
        assert reason is None, f"{path.name} imports forbidden module {name!r}: {reason}"
        assert name in ALLOWED_TOP_LEVEL, (
            f"{path.name} imports {name!r}, which is not on the kernel allowlist. "
            f"Adding a kernel dependency requires a constitution amendment."
        )


def test_kernel_does_not_import_higher_layers() -> None:
    """FR-021 — the kernel is the bottom layer and imports nothing above it."""
    higher = {"ingest", "transform", "extraction", "pipeline", "api", "adapters"}
    for path in kernel_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "docdoc" and len(parts) > 1:
                    assert parts[1] not in higher, (
                        f"{path.name} imports upward from docdoc.{parts[1]} -- "
                        f"the dependency direction is one-way (Principle X)"
                    )


class _AuditRecorder:
    """Records forbidden audit events instead of failing inside the hook.

    Raising from an audit hook can be swallowed or produce confusing tracebacks,
    so violations are collected and asserted afterwards.
    """

    FORBIDDEN_PREFIXES = ("open", "socket.", "subprocess.", "urllib.", "os.system", "os.remove")

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.active = False

    def __call__(self, event: str, args: object) -> None:
        if not self.active:
            return
        if event.startswith(self.FORBIDDEN_PREFIXES):
            self.violations.append(event)


_RECORDER = _AuditRecorder()
sys.addaudithook(_RECORDER)


def test_kernel_operations_perform_no_io() -> None:
    """FR-020 — exercising the whole kernel surface triggers no I/O."""
    from tests.support import make_document

    _RECORDER.violations.clear()
    _RECORDER.active = True
    try:
        doc = make_document(page_breaks=(20,))
        spans = doc.find("INV-001")
        for span in spans:
            doc.locate(span)
        part = doc.slice(doc.pages[0].span)
        docdoc.kernel.Document.merge((part,))
    finally:
        _RECORDER.active = False

    assert _RECORDER.violations == [], (
        f"kernel operations triggered I/O audit events: {_RECORDER.violations}"
    )


def test_kernel_operations_are_repeatable() -> None:
    """FR-019/SC-005 — identical inputs produce identical outputs across runs."""
    from tests.support import make_document

    first = make_document(page_breaks=(20,))
    second = make_document(page_breaks=(20,))

    assert first.id == second.id
    assert first.find("INV-001") == second.find("INV-001")

    span = first.find("INV-001")[0]
    assert first.locate(span) == second.locate(span)
    assert first.slice(first.pages[0].span).id == second.slice(second.pages[0].span).id
