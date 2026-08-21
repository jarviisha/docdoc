"""T075 — scoring the committed tier with the network removed (FR-007).

**This and the import contract are two different checks, and neither replaces the
other.** research.md R1 records that an earlier draft had the relationship
backwards, so it is worth stating plainly:

``lint-imports`` catches the **graph-visible** mistake — importing
``docdoc.extraction`` as a package pulls in its adapter registry and, through it,
``google.genai``. That contract fires on it, and it fired for real on the first
module in this layer that needed ``ExtractionResult``.

This test catches what a graph cannot see: a module that opens a socket by hand,
resolves a host through something the contract does not name, or reaches a
network through a dependency's lazy import. The contract proves what is *written*;
this proves what *happens*.

Run both. Either one alone leaves a hole the other covers.
"""

from __future__ import annotations

import socket

import pytest

from docdoc.evaluation import compare, evaluate
from tests.fixtures.evaluation.datasets import (
    committed_golden_set,
    committed_prediction_set,
    facts_for_fixtures,
)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every route to a socket, closed.

    ``socket.socket`` is the constructor almost everything ends up at, and the
    three module-level helpers beside it are the ways to reach the network
    without calling it — ``create_connection`` builds its own, and the two
    resolvers touch DNS before any socket exists.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "evaluation reached the network. Scoring is a deterministic offline "
            "computation over recorded facts; a metric that needed a network is a "
            "metric nobody can reproduce (FR-007)"
        )

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)


def test_the_committed_public_tier_scores_with_no_network(no_network: None) -> None:
    """The headline: a contributor with a checkout and no connection gets metrics."""
    golden = committed_golden_set()
    predictions = committed_prediction_set()

    report = evaluate(golden, predictions, facts=facts_for_fixtures())

    assert report.metrics.micro["field_accuracy"].value is not None
    assert report.metrics.counts.labelled == 28


def test_loading_the_dataset_needs_no_network(no_network: None) -> None:
    """The load is inside the guarantee, not adjacent to it.

    A loader that fetched a schema or resolved a hash over the network would make
    the guarantee true of ``evaluate`` and false of the thing a contributor runs.
    """
    golden = committed_golden_set()
    predictions = committed_prediction_set()

    assert len(golden.documents) == 6
    assert len(predictions.predictions) == 4


def test_comparing_two_reports_needs_no_network(no_network: None) -> None:
    """US2's path too, since a regression check runs in CI where egress may be closed."""
    facts = facts_for_fixtures()
    golden = committed_golden_set()
    predictions = committed_prediction_set()

    first = evaluate(golden, predictions, facts=facts)
    second = evaluate(golden, predictions, facts=facts)

    assert compare(first, second).metrics["field_accuracy"].delta == 0


def test_no_credential_is_read(monkeypatch: pytest.MonkeyPatch, no_network: None) -> None:
    """Not merely unused — unread.

    ``conftest.py`` already clears credentials for the offline suite. This goes
    further and makes reading the environment at all fatal for the names a
    provider would use, so a module that quietly degraded to "no credentials, no
    grounding" would fail here rather than report a worse number.
    """
    import os

    real_getenv = os.environ.get
    watched = {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DOCDOC_AZURE_DI_ENDPOINT",
        "DOCDOC_AZURE_DI_KEY",
    }

    def guarded(key: str, default: object = None) -> object:
        if key in watched:
            raise AssertionError(f"evaluation read the credential {key!r}")
        return real_getenv(key, default)  # type: ignore[arg-type]

    monkeypatch.setattr(os.environ, "get", guarded)

    report = evaluate(
        committed_golden_set(), committed_prediction_set(), facts=facts_for_fixtures()
    )

    assert report.report_id


def test_the_guard_would_actually_fire(no_network: None) -> None:
    """The guard on the guard.

    A patch applied to the wrong name silently protects nothing, and every test
    above would pass while the network stayed wide open.
    """
    with pytest.raises(AssertionError, match="reached the network"):
        socket.socket()
