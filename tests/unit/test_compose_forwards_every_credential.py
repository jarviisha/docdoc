"""FR-077 — a credential an operator supplies must reach the containers.

"The composition MUST bring up a working deployment with no manual step beyond
supplying a schema path and a provider credential." A variable the containers
never see is not a step an operator can take, and that is exactly what the
composition did: it forwarded `GEMINI_API_KEY` and none of the **parser**
credentials.

The consequence was invisible in every way that matters. `echo` replaces the
*model*, not the parse, and the image ships no local parser because PyMuPDF is
AGPL-3.0 — so on a default build a cloud parser credential is the only route to a
working deployment. Export one, run `docker compose up`, and the containers start,
report healthy, and refuse every document with `ParserCapabilityError`. Nothing
logs a configuration problem, because from docdoc's point of view there is not
one: no parser was configured.

**Read as text rather than parsed as YAML**, deliberately. PyYAML is not a
dependency of this project and adding one for a single test would be a poor
trade — SC-013 measures what a base install pulls in, and the suite is part of
what a contributor installs. The `environment:` block is a flat list of `NAME:`
keys and a substring check over it is enough to answer "is this name forwarded",
which is the whole question.
"""

from __future__ import annotations

import pathlib
import re

import pytest

COMPOSE = pathlib.Path("packaging/docker/compose.yml")
DOCKERFILE = pathlib.Path("packaging/docker/Dockerfile")

#: Every credential a provider adapter or parser reads, by the extra that
#: installs it. Only the extras the image actually installs are required to be
#: forwarded — a credential for code that is not in the image is a variable with
#: nothing to read it.
CREDENTIALS_BY_EXTRA: dict[str, tuple[str, ...]] = {
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "azure": ("DOCDOC_AZURE_DI_ENDPOINT", "DOCDOC_AZURE_DI_KEY"),
    "gcv": ("DOCDOC_GCV_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS"),
}

#: Extras that carry no credential at all. Listed so the map above is checkable
#: against the image rather than trusted: an extra in neither place is one
#: nobody classified.
CREDENTIAL_FREE_EXTRAS = frozenset({"api", "postgres", "s3", "pdf", "ui", "dev"})


def _compose() -> str:
    assert COMPOSE.is_file(), f"{COMPOSE} is missing; this file checks nothing"
    return COMPOSE.read_text(encoding="utf-8")


def _installed_extras() -> set[str]:
    """The extras the image's default build installs, read off the Dockerfile.

    Off the `ARG` default rather than a hand-written list, so that adding an
    extra to the image is enough to make its credentials required here — which
    is the direction this test needs to work in.
    """
    source = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^ARG DOCDOC_EXTRAS=([a-z0-9,\-]+)\s*$", source, re.M)
    assert match, "the Dockerfile no longer declares DOCDOC_EXTRAS; this check is blind"
    return {extra.strip() for extra in match.group(1).split(",") if extra.strip()}


def _forwarded() -> set[str]:
    """Every variable name the shared `environment:` block sets.

    One anchor: both services use the `&docdoc_env` alias, so the block appears
    once and applies to the API and the worker alike. If that stops being true
    this returns the API's alone, which the last test here guards against.
    """
    text = _compose()
    start = text.index("environment: &docdoc_env")
    end = text.index("volumes: &docdoc_mounts", start)
    block = text[start:end]
    return set(re.findall(r"^\s{6}([A-Z][A-Z0-9_]*):", block, re.M))


# -- the map itself is honest --------------------------------------------------


def test_every_installed_extra_is_classified() -> None:
    """Guards the map: an extra in neither list is one nobody thought about."""
    unclassified = _installed_extras() - set(CREDENTIALS_BY_EXTRA) - CREDENTIAL_FREE_EXTRAS
    assert not unclassified, (
        f"the image installs {sorted(unclassified)} and this file does not say "
        f"whether they read a credential. Add each to CREDENTIALS_BY_EXTRA or to "
        f"CREDENTIAL_FREE_EXTRAS, so the answer is a decision rather than a gap"
    )


def test_the_credential_names_are_the_ones_the_code_reads() -> None:
    """The map names variables the adapters genuinely read, not ones invented here.

    A forwarding check against names nothing reads would pass forever and prove
    nothing, which is the failure mode of every hand-written list in this
    repository that is not checked against the code.
    """
    from docdoc.ingest.parsers import azure_di

    assert azure_di.ENDPOINT_ENV in CREDENTIALS_BY_EXTRA["azure"]
    assert azure_di.KEY_ENV in CREDENTIALS_BY_EXTRA["azure"]

    import inspect

    from docdoc.ingest.parsers import gcv

    source = inspect.getsource(gcv)
    for name in CREDENTIALS_BY_EXTRA["gcv"]:
        assert name in source, f"{name} is claimed as a gcv credential and gcv never reads it"


# -- the forwarding ------------------------------------------------------------


@pytest.mark.parametrize(
    "credential",
    sorted({name for names in CREDENTIALS_BY_EXTRA.values() for name in names}),
)
def test_every_credential_the_image_can_use_is_forwarded(credential: str) -> None:
    """FR-077's one permitted manual step, made possible.

    Parameterised per credential so a failure names the one that is missing,
    rather than reporting "some credential" and leaving the reader to diff two
    files.
    """
    installed = _installed_extras()
    needed = {
        name
        for extra, names in CREDENTIALS_BY_EXTRA.items()
        if extra in installed
        for name in names
    }
    if credential not in needed:
        pytest.skip(f"no installed extra reads {credential}")

    assert credential in _forwarded(), (
        f"{credential} is not forwarded by packaging/docker/compose.yml, so an "
        f"operator who supplies it changes nothing: the containers never see it. "
        f"The composition starts, reports healthy, and refuses every document"
    )


def test_a_parser_credential_is_reachable_at_all() -> None:
    """The specific gap, stated as its own assertion.

    The image installs `azure` and `gcv` and deliberately omits `pdf`, so with no
    parser credential forwarded there is **no way at all** to make the default
    build parse anything. This is the one that failed, and it is worth its own
    name so a reader of the failure knows what was broken rather than which
    variable was absent.
    """
    installed = _installed_extras()
    parser_extras = {"azure", "gcv"} & installed
    assert parser_extras, "the image installs no cloud parser; this check is vacuous"

    forwarded = _forwarded()
    reachable = [
        name for extra in parser_extras for name in CREDENTIALS_BY_EXTRA[extra] if name in forwarded
    ]
    assert reachable, (
        "no parser credential reaches the containers, and the default image "
        "ships no local parser (PyMuPDF is AGPL-3.0, ADR-0001). There is "
        "therefore no way to make this composition parse a document, which is "
        "what FR-077 says it must do given one credential"
    )


def test_the_worker_gets_the_same_environment_as_the_api() -> None:
    """FR-041: one configuration vocabulary, and one place it is written.

    The worker reuses the API's block through a YAML anchor. Two literal blocks
    would drift — and the half that drifts is the worker's, because it is the one
    nobody curls.
    """
    text = _compose()

    assert "environment: &docdoc_env" in text, "the shared environment anchor is gone"
    assert text.count("environment: *docdoc_env") >= 1, (
        "the worker no longer reuses the API's environment block, so a credential "
        "added for one process will silently not reach the other"
    )


def test_the_composition_starts_with_no_credential_configured() -> None:
    """Forwarding must not become requiring.

    Every credential is defaulted to empty, so `docker compose up` with none of
    them set still starts — and an unconfigured parser is reported unavailable,
    which is the correct behaviour for a deployment that configured none.
    """
    forwarded_block = _compose()
    for names in CREDENTIALS_BY_EXTRA.values():
        for name in names:
            if f"{name}:" not in forwarded_block:
                continue
            assert f"${{{name}:-}}" in forwarded_block, (
                f"{name} is forwarded without an empty default, so `docker compose "
                f"up` fails or warns for an operator who has not set it"
            )
