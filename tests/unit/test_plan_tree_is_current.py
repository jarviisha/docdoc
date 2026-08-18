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

**The rule does not catch everything, and saying so is the point.** Some of this
feature's test files import nothing from the package, because the package is not
what they test: one runs the examples as subprocesses, one reads test sources to
check marker discipline, one reads the docs, and this one reads the plan. An
import-based scan cannot see any of them.

They were first treated as a two-item escape hatch capped by an assertion that said
growth meant "the rule needs rethinking rather than extending". It grew, and the
rethink is in `META_TESTS` below: these are a *category* — tests of repository
properties rather than package behaviour — not exceptions. The category carries its
own invariant, that no entry may import `docdoc.extraction`, so it cannot be used to
park a real package test out of the derivable rule's view. Claiming the rule was
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


#: **Meta-tests**: files that check a property of the *repository* rather than the
#: behaviour of the package, and therefore import nothing from it.
#:
#: This began as a two-entry escape hatch called NOT_DERIVABLE, capped at two, with
#: an assertion saying that if the list ever grew "the rule needs rethinking rather
#: than extending". The next convergence phase added two more files of exactly this
#: kind, which is the assertion doing its job. The rethink is that these are not
#: exceptions at all — they are a coherent category. A test that asserts the plan's
#: tree is current, or that credential-reading tests carry a marker, or that the
#: documented API resolves, cannot import the package, because the package is not
#: what it is testing.
#:
#: Naming the category is what keeps it from becoming a dumping ground: the
#: invariant below requires that every entry genuinely does *not* import
#: `docdoc.extraction`, so a real package test cannot be parked here to dodge the
#: derivable rule.
META_TESTS = {
    "test_examples_run.py": "runs the examples as subprocesses; asserts they execute",
    "test_plan_tree_is_current.py": "reads the plan and the test tree; this module",
    "test_provider_tests_are_separable.py": "reads test sources for credential reads (FR-045)",
    "test_documented_api_references_resolve.py": "reads the docs; resolves names dynamically",
}


def _extraction_test_files() -> list[pathlib.Path]:
    derived = {path for path in TESTS.rglob("test_*.py") if _imports_extraction(path)}
    named = {path for path in TESTS.rglob("test_*.py") if path.name in META_TESTS}
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


def test_every_meta_test_is_real_and_stays_a_meta_test() -> None:
    """Keeps the category from becoming the route around the derivable rule.

    Two invariants, and the second is the one that matters. A file listed here must
    genuinely *not* import ``docdoc.extraction`` — if it does, the import-based scan
    already covers it, and listing it here would be a way to keep a real package
    test out of view of the rule that governs real package tests.
    """
    on_disk = {path.name for path in TESTS.rglob("test_*.py")}
    for name, reason in META_TESTS.items():
        assert reason, f"{name} is listed with no stated reason"
        assert name in on_disk, (
            f"{name} is listed as a meta-test but does not exist; it was renamed or removed"
        )

    misfiled = sorted(
        path.name
        for path in TESTS.rglob("test_*.py")
        if path.name in META_TESTS and _imports_extraction(path)
    )
    assert not misfiled, (
        f"{misfiled} import docdoc.extraction, so the derivable rule already covers them. "
        "Listing a package test as a meta-test hides it from the check that governs "
        "package tests"
    )


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
