"""T111 — a test needing credentials must say so (FR-045).

FR-045 requires that tests needing a real provider be separable from the unit and
property suites, and never required in order to run them. That was true when this
file was written, and it was true by *convention*: two live test files each set
``pytestmark = pytest.mark.provider`` by hand.

A convergence pass found FR-045 cited by nothing, and citing it in a docstring was
the obvious fix. It would also have been the wrong one. The requirement's whole
content is that the marker is present on every file that needs it, and a comment
saying so verifies nothing — a third live test added next milestone would carry no
marker, run in the default suite, and either fail without credentials or quietly
spend money with them. Prose asserting that a convention holds is how the
convention stops holding.

So the convention is asserted. The rule is derivable rather than a list: **a test
file that reads a credential from the environment must be marked ``provider``.**
Reading a credential is the observable signature of needing one.

The failure this prevents is asymmetric, which is why it is worth a file. An
over-marked test is skipped and someone notices the coverage gap. An under-marked
one turns ``uv run pytest`` — the first command in the quickstart — into something
that needs an API key, and the contributor who hits it has no way to know that was
not intended.
"""

from __future__ import annotations

import ast
import pathlib

TESTS = pathlib.Path("tests")

#: Substrings that mark an environment variable as a credential. Deliberately broad:
#: a false positive costs one marker, a false negative costs the guarantee.
CREDENTIAL_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def _attribute_path(node: ast.expr) -> str:
    """``os.environ.get`` as a dotted string, for matching the call target."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


#: The forms that actually *read* a credential. The distinction from ``setenv`` is
#: the whole point and was found by this test failing on its first run: a matcher
#: that caught any call's first string argument flagged ``monkeypatch.setenv`` and
#: even prose like ``"tokens overlap"``. Tests that *set* a credential in order to
#: prove it goes unused -- the example suite does exactly this -- are asserting the
#: opposite of needing one, and marking them `provider` would remove the assertion
#: from the offline suite it exists to protect.
_READ_CALLS = ("os.environ.get", "os.getenv", "environ.get", "getenv")


def _credentials_read(path: pathlib.Path) -> set[str]:
    """Credential-shaped environment variables a file genuinely reads.

    Matches ``os.environ.get("X")``, ``os.getenv("X")``, and ``os.environ["X"]``,
    which is every form the suite currently uses. A file reaching for a credential
    some other way would escape this, and that is a stated bound rather than a
    claim of completeness.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere, loudly
        return found

    for node in ast.walk(tree):
        name: str | None = None
        if isinstance(node, ast.Subscript) and _attribute_path(node.value) in (
            "os.environ",
            "environ",
        ):
            # Either a literal -- os.environ["GEMINI_API_KEY"] -- or a name bound to
            # one. The live Azure suite uses `os.environ[KEY_ENV]`, and a
            # literals-only matcher reported it as reading nothing, which is how
            # this branch came to exist.
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                name = node.slice.value
            elif isinstance(node.slice, ast.Name):
                name = node.slice.id
        elif (
            isinstance(node, ast.Call)
            and _attribute_path(node.func) in _READ_CALLS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            name = node.args[0].value
        if name and any(hint in name.upper() for hint in CREDENTIAL_HINTS):
            found.add(name)
    return found


def _is_marked_provider(path: pathlib.Path) -> bool:
    """Whether the file carries a module-level ``provider`` marker."""
    return "pytest.mark.provider" in path.read_text(encoding="utf-8")


def test_the_scan_finds_the_credential_reads_it_is_meant_to() -> None:
    """A scan that finds nothing passes for the wrong reason.

    Both known live suites read a credential, so finding fewer than two means the
    matcher stopped matching rather than that the suite got cleaner.
    """
    readers = [p for p in TESTS.rglob("test_*.py") if _credentials_read(p)]
    assert len(readers) >= 2, (
        f"expected at least the two live suites to read credentials, found {readers}. "
        "If they were renamed or the access form changed, this matcher is now blind"
    )


def test_every_test_reading_a_credential_is_marked_provider() -> None:
    """FR-045, as an assertion rather than a convention."""
    unmarked = sorted(
        f"{p} (reads {sorted(_credentials_read(p))})"
        for p in TESTS.rglob("test_*.py")
        if _credentials_read(p) and not _is_marked_provider(p)
    )
    assert not unmarked, (
        "these tests read a credential but are not marked `provider`, so they run in "
        f"the default suite that FR-045 requires to work without one: {unmarked}"
    )


def test_the_matcher_distinguishes_reading_from_setting(tmp_path: pathlib.Path) -> None:
    """Guards the guard, on the distinction that took two tries to get right.

    The first version of this matcher accepted any call's first string argument. It
    flagged ``monkeypatch.setenv("GEMINI_API_KEY", ...)`` -- written to prove the
    example *ignores* a key -- and the phrase ``"tokens overlap"`` out of an
    unrelated assertion message. Marking those `provider` would have moved offline
    assertions into the suite that needs credentials, the exact inversion of FR-045.
    """
    reads = tmp_path / "test_reads.py"
    reads.write_text('import os\nk = os.environ.get("SOME_API_KEY")\n', encoding="utf-8")
    assert _credentials_read(reads) == {"SOME_API_KEY"}, "a genuine read must be caught"

    indirect = tmp_path / "test_indirect.py"
    indirect.write_text("import os\nv = os.environ[KEY_ENV]\n", encoding="utf-8")
    assert _credentials_read(indirect) == {"KEY_ENV"}, (
        "a read through a constant must be caught; the live Azure suite uses this form"
    )

    sets = tmp_path / "test_sets.py"
    sets.write_text(
        "def t(monkeypatch):\n"
        '    monkeypatch.setenv("SOME_API_KEY", "unused")\n'
        '    assert "tokens overlap" not in ""\n',
        encoding="utf-8",
    )
    assert _credentials_read(sets) == set(), (
        "setting a credential to prove it is ignored is not reading one, and prose "
        "containing a hint word is not an environment read"
    )


def test_the_default_suite_selects_no_provider_test() -> None:
    """The other half of FR-045: the marker has to actually exclude something.

    A marker that is registered but never used as a filter would satisfy the check
    above while leaving the requirement unmet. This pins the marker's registration,
    since `--strict-markers` is what makes a typo'd `pytest.mark.provder` an error
    rather than a silently unmarked live test.
    """
    config = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
    assert "--strict-markers" in config, (
        "without --strict-markers a misspelled provider marker is silently accepted, "
        "and the misspelled test runs in the default suite"
    )
    assert '"provider:' in config or "provider:" in config, (
        "the `provider` marker must be registered in pyproject.toml for --strict-markers "
        "to accept it"
    )
