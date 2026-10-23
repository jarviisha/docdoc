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

**Re-pointed at Milestone 012 (the OCR gateway, ADR-0021,
`docs/superpowers/specs/2026-10-19-ocr-gateway-design.md`), 2026-10-19.** Every
`PROTECTED` entry is a layer *this* milestone promised not to touch, not a
permanent list -- the file's whole point is to be re-aimed at whichever
milestone is current, the same way its own history already shows it moving
(SC-020's original citation was Milestone 10's). `src/docdoc/ingest/` came off
the list here because ADR-0021 and the design doc it implements make `ingest/`
exactly what this milestone *is*: a caller naming a configured parser
(`ParserRegistry.select_named`), the PaddleX-protocol adapter, and
`DOCDOC_OCR_ENDPOINTS_FILE` all live there by design, not by accident. This is a
re-scope, not a relaxation (FR-095): the kernel and the three layers downstream
of it stay exactly as protected as before, and nothing here makes it any easier
for a future milestone to touch them unnoticed.

**Re-pointed at Milestone 014 (async OCR through a bounded queue, ADR-0022,
`docs/superpowers/specs/2026-10-19-async-ocr-queue-design.md`), 2026-10-20.**
The protected set is unchanged: 014 adds a run kind, OCR caps, and OCR
retention, all in `runs/`, `api/`, and the pipeline runner, and touches none of
the layers above. Only the guard-the-guard check moved, from `ingest/` to
`runs/`, which is where this milestone's work is.

**Re-pointed at the PDFium parser (ADR-0025,
`docs/superpowers/specs/2026-10-22-pdfium-parser-design.md`), 2026-10-22.**
The swap lives in `ingest/`, so the guard-the-guard check moved there. `kernel/`
and `extraction/` came off the list: they mention PyMuPDF in docstrings and
comments that the swap makes untrue, and those are rewritten. No code in either
changes. `grounding/`, `validation/`, and the identity derivations stay
protected, because a parser swap that moved one of them would be the bug this
file exists to catch.

**Re-pointed at tenant quality evaluation (ADR-0026,
`docs/superpowers/specs/2026-10-22-tenant-quality-evaluation-design.md`),
2026-10-23.** The work is checks stored as answers and evaluations run over
them, so the guard-the-guard check moved to `runs/corrections.py`.
`src/docdoc/evaluation/` is deliberately not protected: a correction can now
confirm a value, which changes `Correction`. `grounding/`, `validation/`, and
the identity derivations stay protected, because measuring extraction quality
must not move a value, a verdict, a location, or an identity.
"""

from __future__ import annotations

import subprocess

import pytest

#: The layers tenant quality evaluation must not touch. `evaluation/` and `runs/`
#: are this work -- see the module docstring.
PROTECTED = (
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


def _head() -> str | None:
    """This checkout's commit, or ``None`` when `git` cannot say."""
    found = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return found.stdout.strip() if found.returncode == 0 else None


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
            base = found.stdout.strip()

            # **On `main` itself there is no branch to assess**, and this file
            # asks a question only a branch can answer: *what did this milestone
            # change*. After a merge, `HEAD` **is** the merge base, the diff is
            # empty by arithmetic rather than by anything anybody did, and
            # `test_the_diff_is_not_empty` fires — correctly, because an empty
            # diff would satisfy every "does not contain" assertion above it.
            #
            # That is what turned CI red on `main` from the moment Milestone 9
            # merged, and again for 10: the pull request ran green, the merge
            # commit ran red, and the run that went red was the one nobody was
            # looking at. Skipping here is not relaxing the guard — a branch
            # with real work still gets every assertion, and a branch whose diff
            # is unexpectedly empty still fails. It is declining to ask a
            # question that has no subject.
            if base == _head():
                pytest.skip(
                    "HEAD is the merge base with `main`, so this is `main` itself "
                    "and there is no branch to assess. This file reads a diff "
                    "against `main`; on `main` there is none by arithmetic"
                )
            return base

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
    assert "src/docdoc/runs/corrections.py" in changed, (
        "this milestone's work is tenant quality evaluation (ADR-0026), and the diff "
        "shows none of it. The comparison is against the wrong ref"
    )
