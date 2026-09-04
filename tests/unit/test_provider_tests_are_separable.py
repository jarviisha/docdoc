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

import pytest

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
        elif isinstance(node, ast.Call) and _attribute_path(node.func) in _READ_CALLS and node.args:
            # A literal -- os.environ.get("GEMINI_API_KEY") -- or a name bound to
            # one. The `Name` half was missing and it cost something real:
            # `gcv.py` reads `os.environ.get(GOOGLE_CREDENTIALS_ENV)`, so this
            # matcher saw no credential there, and `GOOGLE_APPLICATION_CREDENTIALS`
            # went unscrubbed for four milestones. The `Subscript` branch above
            # already handled both forms; this one handled one.
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                name = argument.value
            elif isinstance(argument, ast.Name):
                name = argument.id
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


# -- the other half of the offline guarantee (T119) ---------------------------
#
# The marker rule above stops a credential-needing test from running in the
# offline suite. This stops the reverse: an offline test whose result depends on
# credentials or configuration that happen to be set. Both are SC-019 — "a
# contributor runs 100% of the unit and property suites" — and neither implies
# the other.


def test_no_docdoc_configuration_reaches_an_offline_test() -> None:
    """T119, SC-019 — the suite's result must not depend on the developer's shell.

    Asserted rather than trusted, because it was already broken once: Milestone 3
    added three `DOCDOC_*` variables, guarded the two test files it was editing,
    and left `test_gemini_mapping.py` red for anyone who had `DOCDOC_GEMINI_MODEL`
    set — which is to say, for anyone whose machine was configured to actually use
    the thing. The autouse fixture in `tests/conftest.py` clears them; this is what
    notices if that fixture is removed, narrowed, or stops matching a new name.
    """
    import os

    leaked = sorted(name for name in os.environ if name.startswith("DOCDOC_"))
    assert not leaked, (
        f"{leaked} reached an offline test. tests/conftest.py clears DOCDOC_* for every "
        "non-provider test so a configured machine and a bare one agree (SC-019)"
    )


def test_no_credential_reaches_an_offline_test() -> None:
    """T119, SC-019, FR-045 — and a contributor with keys gets the same result.

    The complement of `test_every_test_reading_a_credential_is_marked_provider`.
    That one asserts no offline test *reads* a credential; this one asserts none
    is there to be read, so a test that starts reading one by accident cannot pass
    on the maintainer's laptop and fail in CI.
    """
    import os

    from tests.conftest import CREDENTIAL_ENV

    present = sorted(name for name in CREDENTIAL_ENV if name in os.environ)
    assert not present, (
        f"{present} reached an offline test; tests/conftest.py should have cleared it"
    )


# -- the scrub list, checked against the code (T111) ---------------------------
#
# `conftest.py`'s `CREDENTIAL_ENV` is hand-maintained, and it went stale in the
# way every hand-maintained list in this repository eventually does:
# `GOOGLE_APPLICATION_CREDENTIALS` was never added, and it is set on any machine
# with `gcloud` configured. With it set the `gcv` parser reports **available**
# where CI reports unavailable, so routing — and therefore which parser an
# offline test exercises — differed between a contributor's machine and the
# runner. The failure lands on the machine that is *correctly* configured.
#
# The remedy is the one `FLAG_FOR_SETTING` and `CONFIG_MODULES` already use: keep
# the list, and check it against the code rather than trusting it.


SOURCE = pathlib.Path("src/docdoc")


def _constants() -> dict[str, str]:
    """Every module-level ``NAME = "VALUE"`` string in ``src/docdoc``.

    Needed because the code reads through a constant — `os.environ[KEY_ENV]`,
    not `os.environ["DOCDOC_AZURE_DI_KEY"]` — so the scan returns the identifier
    and this resolves it to the variable a shell would actually set.

    Repository-wide rather than per file, because a constant is routinely defined
    in one module and read in another: `docdoc.api.auth` reads
    `API_KEYS_FILE_ENV`, which `docdoc.api.settings` defines. A per-file map left
    that one unresolved and reported the *identifier* as an unscrubbed credential,
    which is a confusing way to be right.
    """
    resolved: dict[str, str] = {}
    for path in SOURCE.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - a broken source file fails elsewhere
            continue
        for node in tree.body:
            targets = (
                [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
            )
            value = getattr(node, "value", None)
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    resolved[target.id] = value.value
    return resolved


def _credentials_the_code_reads() -> set[str]:
    """Every credential-shaped environment variable ``src/docdoc`` reads.

    Reuses `_credentials_read`, which is this file's own AST matcher, rather than
    a second regex — a regex over source picks up JSON keys (`VALUE_KEY`) and
    constants (`MAX_TOKENS`), and the first version of this did exactly that.
    What distinguishes a credential from a string containing "KEY" is that the
    code passes it to an environment read, which is a shape only the AST sees.
    """
    constants = _constants()
    found: set[str] = set()
    for source in SOURCE.rglob("*.py"):
        for name in _credentials_read(source):
            found.add(constants.get(name, name))
    return found


def _environment_names_the_code_reads() -> set[str]:
    """Every environment variable the code reads, credential-shaped or not.

    Wider than the above on purpose: `DOCDOC_AZURE_DI_ENDPOINT` holds no secret
    and belongs in the scrub list all the same, because clearing the key while
    leaving the endpoint set is a half-cleared provider — and half a credential
    still changes what the offline suite does.
    """
    constants = _constants()
    found = {value for name, value in constants.items() if name.endswith("_ENV")}
    found |= _credentials_the_code_reads()
    return found


def test_every_credential_the_code_reads_is_scrubbed_for_the_offline_suite() -> None:
    """SC-019, from the direction the hand-written list cannot see.

    A credential is scrubbed either by the `DOCDOC_` prefix sweep or by being
    named in `CREDENTIAL_ENV`. One that is neither makes the offline suite depend
    on the developer's shell — which is the thing `conftest.py` exists to prevent
    and the thing it stopped doing for one variable.
    """
    from tests.conftest import CREDENTIAL_ENV

    unscrubbed = sorted(
        name
        for name in _credentials_the_code_reads()
        if not name.startswith("DOCDOC_") and name not in CREDENTIAL_ENV
    )

    assert not unscrubbed, (
        f"these credentials are read by the code and cleared by nothing: "
        f"{unscrubbed}. A contributor who has one set runs a different suite "
        f"than CI does — and it is the contributor with a *correctly configured* "
        f"machine who gets the failure. Add each to tests/conftest.py's "
        f"CREDENTIAL_ENV"
    )


def test_the_scrub_list_names_credentials_that_exist() -> None:
    """The other direction: an entry for a variable nothing reads.

    Not fatal, but it means the list is describing a provider that was removed,
    and a list describing nothing is one nobody trusts enough to maintain.

    Checked against every environment name the code reads rather than against the
    credential-shaped ones, because `DOCDOC_AZURE_DI_ENDPOINT` is legitimately in
    the list and holds no secret: clearing a provider's key and leaving its
    endpoint set is a half-cleared provider.
    """
    from tests.conftest import CREDENTIAL_ENV

    read = _environment_names_the_code_reads()
    phantom = sorted(name for name in CREDENTIAL_ENV if name not in read)

    assert not phantom, (
        f"cleared by conftest.py and read by no module: {phantom}. The provider "
        f"was removed or renamed and this list now describes nothing"
    )


def test_the_credential_sweep_can_actually_fail() -> None:
    """Guards the guard: a regex over source that matches everything or nothing."""
    read = _credentials_the_code_reads()

    assert "GEMINI_API_KEY" in read, "a known credential must be found"
    assert "GOOGLE_APPLICATION_CREDENTIALS" in read, (
        "the variable that motivated this check is not being found, so the check "
        "would not have caught the gap it exists for"
    )
    assert "DOCDOC_SCHEMA_PATHS" not in read, "a plain setting must not be swept up"


def test_a_stray_google_credential_does_not_change_parser_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured symptom, pinned rather than argued.

    This is what made the gap real rather than theoretical: setting the variable
    flipped `gcv` from unavailable to available. The autouse fixture in
    `conftest.py` now clears it, so this test — which sets it *after* that
    fixture has run — is the honest check that the clearing is what matters, not
    the ordering.
    """
    from docdoc.ingest.registry import default_registry

    before = {parser.id: parser.available for parser in default_registry().candidates_all()}
    assert before["gcv"] is False, (
        "gcv is available before this test sets anything, so the environment is "
        "already contaminated and the assertion below proves nothing"
    )

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/not-a-real-credential.json")
    after = {parser.id: parser.available for parser in default_registry().candidates_all()}

    assert after["gcv"] is True, (
        "setting GOOGLE_APPLICATION_CREDENTIALS no longer changes gcv's "
        "availability. If that is now true by design, this test and the "
        "CREDENTIAL_ENV entry it guards are both describing a problem that has "
        "gone away — remove them rather than leaving a claim that cannot fail"
    )
