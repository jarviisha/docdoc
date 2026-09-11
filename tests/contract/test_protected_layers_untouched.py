"""T173a, SC-020, FR-094, FR-095 — the layers this milestone did not touch.

plan.md's first constitution gate reads *"the kernel is not touched; SC-020
asserts zero files changed under it"*, and until this file existed that citation
pointed at nothing. A gate whose evidence does not exist is a gate that passed
because somebody said so.

So the diff against `main` is read, and the answer has to be zero.

**This is a `git`-shaped test, and it says so when it cannot run.** On a shallow
clone or a detached checkout with no merge base there is nothing to diff against;
it skips with the reason rather than passing quietly, because a guard that
silently no-ops in CI is worse than one that is absent.
"""

from __future__ import annotations

import subprocess

import pytest

#: Every layer Milestone 10 declared it would not touch. `kernel` first, because
#: it is the one Principle I protects by name and the one whose purity is
#: enforced by an AST scan and a runtime audit hook.
PROTECTED = (
    "src/docdoc/kernel/",
    "src/docdoc/ingest/",
    "src/docdoc/extraction/",
    "src/docdoc/grounding/",
    "src/docdoc/validation/",
)

#: Two files inside a layer that *was* touched. `artifacts` gained deletion, so
#: the whole package cannot be listed above — but the identity derivations are
#: what ADR-0011 protects, and this milestone changed neither.
PROTECTED_FILES = (
    "src/docdoc/artifacts/derivation.py",
    "src/docdoc/artifacts/envelope.py",
)

#: The guards themselves. FR-095 forbids relaxing them, and the shape of that
#: failure is a change written to make a guard pass which also removes what the
#: guard was watching — which is exactly what happened to
#: `test_runs_clock_confinement.py` once already.
GUARDS = (
    "tests/unit/test_kernel_purity.py",
    "tests/unit/test_runs_clock_confinement.py",
)


def _merge_base() -> str:
    """Where this branch left `main`, or a skip naming why there is none."""
    for ref in ("origin/main", "main"):
        found = subprocess.run(
            ["git", "merge-base", "HEAD", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()

    pytest.skip(
        "no merge base with `main` is reachable, so there is no diff to read. "
        "This is a shallow clone or a detached checkout; fetch `main` to run it"
    )
    raise AssertionError  # unreachable; `skip` raises


def _changed() -> set[str]:
    """Every path this branch changed since it left `main`, **including uncommitted.**

    Against the *working tree* rather than against `HEAD`, and that is not a
    convenience. A comparison to `HEAD` answers "what has been committed", so it
    reports zero for work in progress — and this check's whole value is that it
    fires while somebody is still editing, rather than after they have pushed a
    change to the kernel.

    Untracked files are included for the same reason: a new file under
    `src/docdoc/kernel/` is as much a change to that layer as an edit to an
    existing one, and `git diff` alone does not see it.
    """
    base = _merge_base()
    changed: set[str] = set()

    for command in (
        ["git", "diff", "--name-only", base],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            pytest.skip(f"{' '.join(command)} failed: {result.stderr.strip()}")
        changed |= {line.strip() for line in result.stdout.splitlines() if line.strip()}

    return changed


@pytest.mark.parametrize("layer", PROTECTED)
def test_no_file_changed_under_a_protected_layer(layer: str) -> None:
    """**SC-020.** Zero, and the number is the assertion.

    This is what makes the milestone's central claim checkable: every capability
    here is operational, and none of them can have moved a value, a verdict, a
    location, or an identity — because none of them touched the code that
    produces one.
    """
    offending = sorted(path for path in _changed() if path.startswith(layer))

    assert not offending, (
        f"this milestone changed files under {layer}: {offending}. SC-020 asserts "
        f"zero, and plan.md's constitution gate 1 cites it as its only evidence"
    )


@pytest.mark.parametrize("path", PROTECTED_FILES)
def test_the_identity_derivations_are_unchanged(path: str) -> None:
    """`artifacts` gained deletion and its identity derivations gained nothing.

    ADR-0011 names the on-disk format and the identity derivations as the two
    surfaces that need a deprecation path. The protocol change this milestone
    made is neither, which is why a changelog entry was enough for it — and why
    this has to be true for that argument to hold.
    """
    assert path not in _changed(), (
        f"{path} changed. A moved identity derivation makes every stored artifact "
        f"unreachable at the id it was stored under (ADR-0011, FR-094)"
    )


@pytest.mark.parametrize("guard", GUARDS)
def test_the_guards_were_not_relaxed(guard: str) -> None:
    """FR-095, and it is the requirement with the worst failure mode.

    `test_runs_clock_confinement.py`'s own docstring records that it was once
    silenced by a change written to avoid a false positive — "the precise failure
    mode a guard that cannot see provenance will always have". A guard modified
    in the same milestone as the code it watches is a guard that stopped being
    evidence.
    """
    assert guard not in _changed(), (
        f"{guard} was modified by this milestone. FR-095 forbids relaxing the "
        f"determinism guards, and a guard edited alongside the code it watches is "
        f"no longer independent evidence about it"
    )


def test_the_diff_is_not_empty() -> None:
    """Guards the guard.

    Every assertion above is of the form "this set does not contain X", and an
    empty set satisfies all of them. If the diff came back empty — a bad merge
    base, a `git` that answered nothing — this file would report a milestone that
    changed no protected file *and* no other file either.
    """
    changed = _changed()

    assert changed, "the diff against `main` is empty, so nothing above was checked"
    assert any(path.startswith("src/docdoc/runs/") for path in changed), (
        "this milestone's work is in `docdoc.runs`, and the diff shows none of "
        "it. The comparison is against the wrong ref"
    )
