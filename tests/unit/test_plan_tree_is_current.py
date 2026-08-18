"""T106 — the plan's test tree stays current (plan: project structure).

That tree went stale three times. An analysis pass named three missing files; a
convergence phase added the two *that finding named*; the next pass found six
more. Each fix addressed the instances it happened to list, which guaranteed the
next pass would find others.

The obvious response is that enumerating test files in a plan is the wrong shape
and the tree should describe directories. That was considered and rejected: every
line in it maps a requirement — an `EXT-`, `SC-`, or `FR-` — to the test that
holds it, and no other artifact provides that map. A reviewer asking "where is
SC-016 checked?" has one place to look.

So it is kept and checked. The mechanical core of the rule is: **a test file that
imports `docdoc.extraction` belongs in this feature's plan.** Same shape as the
adapter-coverage and example-coverage assertions, both added because a
hand-maintained list goes stale exactly when nobody is looking at it.

**The rule does not catch everything, and saying so is the point.** Two of this
feature's own test files import nothing from the package: one runs the examples as
subprocesses, and this one reads files. An import-based rule cannot see them, so
they are listed explicitly below — and `test_the_allowlist_stays_small` keeps that
list from becoming the place things go to escape the check. Claiming the rule was
fully derivable would be the same species of overclaim this file exists to end.
"""

from __future__ import annotations

import ast
import pathlib

PLAN = pathlib.Path("specs/003-schema-driven-extraction/plan.md")
TESTS = pathlib.Path("tests")


def _imports_extraction(path: pathlib.Path) -> bool:
    """Whether a test file reaches into `docdoc.extraction` at all."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere, loudly
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("docdoc.extraction"):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name.startswith("docdoc.extraction") for alias in node.names
        ):
            return True
    return False


#: In scope for this feature but invisible to an import-based scan, because they
#: exercise it without importing it. Each is here for a stated reason, not by
#: default.
NOT_DERIVABLE = {
    "test_examples_run.py": "runs the examples as subprocesses; imports nothing",
    "test_plan_tree_is_current.py": "reads files; this module",
}


def _extraction_test_files() -> list[pathlib.Path]:
    derived = {path for path in TESTS.rglob("test_*.py") if _imports_extraction(path)}
    named = {path for path in TESTS.rglob("test_*.py") if path.name in NOT_DERIVABLE}
    return sorted(derived | named)


def test_the_scan_finds_the_tests_it_is_meant_to() -> None:
    """A check that scans nothing passes for the wrong reason."""
    found = _extraction_test_files()
    assert len(found) >= 15, f"expected the extraction suite to be substantial, found {found}"


def test_every_extraction_test_file_appears_in_the_plan() -> None:
    """The assertion that ends three rounds of the same finding."""
    plan = PLAN.read_text(encoding="utf-8")
    missing = [path.name for path in _extraction_test_files() if path.name not in plan]
    assert not missing, (
        f"these test files exercise docdoc.extraction but are absent from {PLAN}: "
        f"{sorted(missing)}. The tree maps each requirement to the test that holds it, so a "
        "file missing from it is a requirement a reviewer cannot trace"
    )


def test_the_plan_names_no_test_file_that_does_not_exist() -> None:
    """The other direction. A tree naming a deleted file misleads just as much."""
    import re

    plan = PLAN.read_text(encoding="utf-8")
    named = set(re.findall(r"\btest_[a-z0-9_]+\.py\b", plan))
    on_disk = {path.name for path in TESTS.rglob("test_*.py")}
    phantom = sorted(named - on_disk)
    assert not phantom, (
        f"{PLAN} names test files that do not exist: {phantom}. Either they were renamed "
        "and the tree was not, or they were never written"
    )


def test_the_allowlist_stays_small() -> None:
    """Keeps the escape hatch from becoming the route.

    Every entry carries its reason. If this grows, the import-based rule is
    covering less than it looks like it covers, and that is worth noticing rather
    than absorbing.
    """
    assert len(NOT_DERIVABLE) <= 2, (
        f"the non-derivable list has grown to {sorted(NOT_DERIVABLE)}; if test files "
        "routinely exercise this feature without importing it, the rule needs rethinking "
        "rather than extending"
    )
    for name, reason in NOT_DERIVABLE.items():
        assert reason, f"{name} is exempted with no stated reason"


def test_the_check_can_actually_fail() -> None:
    """Guards the guard.

    The matcher is a substring search against a markdown file, which is the kind
    of thing that quietly matches everything or nothing. This confirms it
    distinguishes a listed name from an unlisted one.
    """
    plan = PLAN.read_text(encoding="utf-8")
    assert "test_conform.py" in plan, "a file known to be listed must be found"
    assert "test_a_file_that_was_never_written.py" not in plan, (
        "an unlisted name must not be found, or the check matches anything"
    )
