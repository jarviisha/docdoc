"""T117, T118 — the two limits an operator has to be told, told where they read.

spec.md's Assumptions section carries two entries that are not assumptions at all
but obligations wearing an assumption's clothing:

    "Static credentials are sufficient for this milestone, provided the
     key-rotation limitation is *documented as a limitation* rather than left to
     be discovered."

    "Polling is sufficient for this milestone, provided the *documentation states
     a sane interval*."

Both were unsatisfied for six convergence passes, and the reason is instructive:
nothing traced to them. A requirement phrased as an assumption is invisible to a
sweep that walks FR-### and SC-###, and each of these was in fact *half* done —
the rotation caveat existed in `api/auth.py`'s class docstring, the polling
interval existed as a constant in an example. Both were true statements sitting
where the person who needs them will not look. An operator does not read a class
docstring, and a client author copies a constant without reading the module it
came from.

**So this checks placement, not existence.** The gap was never "nobody wrote it
down"; it was "it is written down somewhere that does not count". A test that
searched the whole tree would have passed on the day the gap was found.

What it deliberately does not do is match the prose. Asserting a sentence would
make this a spell-checker that fails on every rewording, and the useful invariant
is narrower: the *auth section* of each operator document says a restart is
involved, and *some* document states a poll interval in seconds. Rewrite the
paragraphs freely; delete the caveat and this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Where an operator turns on authentication. Each entry is a file and the heading
#: its auth discussion lives under -- the heading matters, because a mention of
#: "restart" three sections away in a paragraph about upgrades is not the reader
#: learning that revocation needs one.
AUTH_SECTIONS = (
    ("README.md", "**Authentication exists, and it is off by default.**"),
    ("examples/serve_api.md", "## Authentication"),
    ("docs/concepts/runs.md", "## Tenants"),
)

#: The documents that describe polling as a thing a client does.
POLLING_DOCUMENT = "docs/concepts/runs.md"

#: The example whose interval is the one a client author is most likely to copy.
POLLING_EXAMPLE = "examples/submit_async_run.py"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The heading and everything until the next one at the same level or above.

    Bounding the search is the whole point of this file. An unbounded ``in``
    against the document would pass on a "restart" belonging to some other topic.

    *At the same level or above* is the load-bearing half. Stopping at the next
    heading of any depth would end the section at its own first subheading, which
    is exactly where a document puts the detail — and it did: this cut
    ``## Authentication`` off immediately before ``### Rotating a key means
    restarting``, the subsection written to satisfy the requirement being checked.
    """
    start = text.find(heading)
    assert start != -1, f"the section {heading!r} is gone; this test needs re-aiming"
    depth = len(heading) - len(heading.lstrip("#"))
    body = text[start + len(heading) :]
    # A heading that is not one -- the README's bolded lead-in -- is bounded by
    # any heading at all, since it has no level of its own to compare against.
    end = re.search(rf"^#{{1,{depth or 3}}} ", _outside_code(body), flags=re.MULTILINE)
    return body[: end.start()] if end else body


def _outside_code(text: str) -> str:
    """``text`` with fenced blocks blanked, same length, for boundary detection.

    A shell comment is ``# like this``, which is also a level-one markdown
    heading, and the rotation procedure this file checks for is written as
    commented ``bash``. Without this the section ended at its own first code
    block — passing or failing on whether the documentation happened to explain
    itself in prose or in a snippet.

    Same length so offsets found here still index into the original.
    """
    out, fenced = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        out.append(" " * (len(line) - 1) + "\n" if fenced and line.endswith("\n") else line)
    return "".join(out)


# -- T117: revoking a key requires a restart, and the reader is told ------------


@pytest.mark.parametrize(("document", "heading"), AUTH_SECTIONS, ids=lambda value: str(value))
def test_the_key_rotation_limitation_is_stated_where_keys_are_configured(
    document: str, heading: str
) -> None:
    """The failure this documents is silent, which is why it has to be written down.

    Delete a compromised key from the file and there is no error, no warning, and
    no change in behaviour — the process is still answering from the mapping it
    read at startup. An operator who believes a file edit revoked a credential is
    wrong in the one direction that matters, and nothing in the system will tell
    them.
    """
    section = _section(_read(document), heading)

    assert "restart" in section.lower(), (
        f"{document}'s auth section does not mention a restart. Revoking a key "
        "has no effect until every process holding the old mapping restarts, and "
        "an operator who is not told that will believe an edit revoked a "
        "credential when it did not (spec.md, 'Static credentials are "
        "sufficient…provided the key-rotation limitation is documented')."
    )


def test_the_caveat_names_the_thing_that_does_not_happen() -> None:
    """ "Restart to rotate" and "an edit alone does nothing" are different claims.

    The first reads as a deployment convention. The second is the security-shaped
    half: it tells the operator that the key they just deleted is *still working*.
    Only the long-form document is held to it — the README and the concept page
    may summarise.
    """
    section = _section(_read("examples/serve_api.md"), "## Authentication").lower()

    assert "keeps working" in section or "still" in section, (
        "examples/serve_api.md explains how to rotate but not what happens if you "
        "only delete: the removed key keeps answering until the process restarts, "
        "which is the part an operator needs before they treat an edit as a "
        "revocation"
    )


# -- T118: a stated polling interval, and an example that disclaims its own ----


def test_a_polling_interval_is_stated_in_seconds() -> None:
    """A client author picking an interval should not have to guess.

    Before this, the only number anywhere was the example's ``POLL_SECONDS =
    0.25`` — four requests a second, per run, against the database the workers
    claim from.
    """
    text = _read(POLLING_DOCUMENT)

    assert re.search(r"\bpoll\b", text, flags=re.IGNORECASE), (
        f"{POLLING_DOCUMENT} no longer discusses polling"
    )
    #: Matches "2-5 seconds", "2 s", or the same written with an en dash, which is
    #: spelled as an escape because a literal one reads as a hyphen on the page.
    interval = "\\b\\d+(?:[-\\u2013]\\d+)?\\s*(?:s\\b|second)"
    assert re.search(interval, text, flags=re.IGNORECASE), (
        f"{POLLING_DOCUMENT} describes polling without stating an interval. "
        "There is no push and no webhook, so every client has to choose one, and "
        "an unstated default becomes whatever the first example showed "
        "(spec.md, 'Polling is sufficient…provided the documentation states a "
        "sane interval')."
    )


def test_the_example_says_its_own_interval_is_not_the_recommendation() -> None:
    """The example polls fast so it finishes while you watch it. That is fine.

    What is not fine is a constant that looks like advice. This asserts the
    disclaimer sits with the constant rather than elsewhere in the file, because
    the way this number escapes is somebody copying the two lines it lives on.
    """
    text = _read(POLLING_EXAMPLE)
    declaration = text.find("POLL_SECONDS =")
    assert declaration != -1, "the example no longer has a poll interval to disclaim"

    preamble = text[max(0, declaration - 700) : declaration].lower()

    assert "should" in preamble or "purpose" in preamble, (
        "POLL_SECONDS carries no note saying it is faster than a real client "
        "should poll. It is the only interval in the repository a reader can "
        "copy, and it is four requests per second per run."
    )
