"""FR-020's cache, and the three things it must be true about.

ADR-0006 specified that the comparison-time match view is cached by
``document_id`` + ``match_view_version``, and grounding did not do it until
Milestone 7. T058 asked for these tests and the first implementation pass marked
it done having written only two timing assertions in the perf suite — which can
see that the cache is *fast* and cannot see that it is *right*.

The distinction matters here more than usual. This cache already shipped one real
defect: the key it was given does not determine the value. A ``document_id`` folds
the blob, the parser, and the options, never the text — which is sound for a
parsed document, where those determine the text, and unsound for a ``Document``
built by hand. The cache returned a view folded from different text, and the
matcher then resolved spans into a document that never contained those characters.
The full suite caught it; nothing in this file's original scope would have.
"""

from __future__ import annotations

import pytest

from docdoc.grounding.ground import ground
from docdoc.grounding.view import _VIEWS as VIEWS
from docdoc.grounding.view import (
    MATCH_VIEW_CACHE_ENV,
    MATCH_VIEW_VERSION,
    MatchView,
    clear_view_cache,
)
from tests.support import make_document, make_extracted, make_extraction


@pytest.fixture(autouse=True)
def _cold() -> None:
    """A cold cache per test.

    Autouse because the cache is process-global by design — it is a
    process-local optimisation, not per-call state — and a test that inherited
    another test's entries would be asserting against whatever ran before it.
    """
    clear_view_cache()


def _document(text: str):  # type: ignore[no-untyped-def]
    return make_document(text)


# -- it is a cache ----------------------------------------------------------


def test_a_second_build_of_one_document_returns_the_cached_view() -> None:
    document = _document("ACME LTD\nINV-001\nTotal 1,240.00\n")

    first = MatchView.build(document)
    second = MatchView.build(document)

    assert second is first, "the view was rebuilt for a document already folded"
    assert len(VIEWS) == 1


def test_a_cached_view_is_identical_to_a_freshly_built_one() -> None:
    """The correctness half, which the perf suite cannot see.

    A cache that returns something *nearly* right is worse than no cache: the
    difference appears as a grounding result that is subtly wrong and carries
    every mark of being correct.
    """
    document = _document("Facture n° 42 — Société Générale\nTotal à payer 1 240,00\n")

    cached = MatchView.build(document)
    clear_view_cache()
    fresh = MatchView.build(document)

    assert cached is not fresh
    assert cached.text == fresh.text
    assert cached.view_id == fresh.view_id
    assert cached.version == fresh.version == MATCH_VIEW_VERSION
    assert cached.offsets.segments == fresh.offsets.segments


def test_grounding_outcomes_are_identical_cold_and_warm() -> None:
    """The assertion that actually protects a result.

    Everything above is about the view; this is about what grounding *decides*
    with it. Run twice over the same inputs, once cold and once warm, and every
    outcome must match — status, score, span, page, and geometry.
    """
    document = _document("ACME LTD\nINV-001\nTotal 1,240.00\nDue 2026-03-01\n")
    extraction = make_extraction(
        {
            "supplier": make_extracted("supplier", value="ACME LTD", claimed_text="ACME LTD"),
            "number": make_extracted("number", value="INV-001", claimed_text="INV-001"),
            "total": make_extracted("total", value="1240.00", claimed_text="1,240.00"),
            "missing": make_extracted("missing", value="nowhere", claimed_text="nowhere"),
        },
        document=document,
    )

    clear_view_cache()
    cold = ground(document, extraction)
    warm = ground(document, extraction)

    assert warm.artifact_id == cold.artifact_id
    assert warm.counts == cold.counts
    assert warm.outcomes == cold.outcomes


# -- the bound --------------------------------------------------------------


def test_the_bound_is_honoured_and_evicts_least_recently_used() -> None:
    """FR-020 — "bounded by a stated maximum number of entries", evicting LRU.

    An unbounded cache over a corpus sweep is a memory profile nobody chose, and
    a bound that is never enforced is the same thing with a comment on it.
    """
    from docdoc.grounding.view import _ViewCache

    cache = _ViewCache(limit=2)
    views = {name: MatchView.build(_document(f"document {name}\n")) for name in "abc"}

    cache.put("a", "document a\n", views["a"])
    cache.put("b", "document b\n", views["b"])
    assert len(cache) == 2

    # Touch "a" so "b" becomes the least recently used.
    assert cache.get("a", "document a\n") is views["a"]

    cache.put("c", "document c\n", views["c"])
    assert len(cache) == 2, "the bound was exceeded"
    assert cache.get("b", "document b\n") is None, "the wrong entry was evicted"
    assert cache.get("a", "document a\n") is views["a"]
    assert cache.get("c", "document c\n") is views["c"]


def test_a_bound_of_zero_disables_the_cache_rather_than_erroring() -> None:
    """Configuration should be able to turn it off, and off must mean off."""
    from docdoc.grounding.view import _ViewCache

    cache = _ViewCache(limit=0)
    view = MatchView.build(_document("anything\n"))

    cache.put("a", "anything\n", view)
    assert len(cache) == 0
    assert cache.get("a", "anything\n") is None


def test_the_bound_is_configurable_and_falls_back_on_nonsense(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in a cache size must not stop a document being processed.

    The default is always a safe answer, because the cache changes no result.
    """
    from docdoc.grounding.view import MATCH_VIEW_CACHE_LIMIT, _configured_limit

    monkeypatch.setenv(MATCH_VIEW_CACHE_ENV, "3")
    assert _configured_limit() == 3

    monkeypatch.setenv(MATCH_VIEW_CACHE_ENV, "not-a-number")
    assert _configured_limit() == MATCH_VIEW_CACHE_LIMIT

    monkeypatch.setenv(MATCH_VIEW_CACHE_ENV, "-5")
    assert _configured_limit() == 0, "a negative bound means no cache, not an error"


# -- the key does not determine the value -----------------------------------


def test_a_document_whose_text_differs_is_never_served_the_cached_view() -> None:
    """The defect this cache actually shipped, pinned.

    ``document_id`` folds the blob, the parser, and the options — never the text.
    For a parsed document those determine the text and the key is sound; for a
    ``Document`` built in memory they do not. Serving the cached view then hands
    the matcher an offset map for text the document does not contain, and every
    span it returns points somewhere that never existed.
    """
    from docdoc.grounding.view import _view_id_for

    first = _document("the first document, which is longer than the second\n")
    second = _document("short\n")

    view = MatchView.build(first)
    assert view is MatchView.build(first)

    # Force the collision the real defect arrived by: one key, two texts.
    key = _view_id_for(first.id, MATCH_VIEW_VERSION)
    assert VIEWS.get(key, first.text) is view
    assert VIEWS.get(key, second.text) is None, (
        "the cache served a view folded from different text; the offsets it "
        "carries do not describe the document that asked for it"
    )


def test_two_documents_never_share_a_view_even_when_they_share_a_key() -> None:
    """And on synthetic documents they *do* share a key, which is the point.

    ``make_document`` derives ``document_id`` from a fixed blob, parser, and
    options, so two documents with different text get the **same** identity — and
    therefore the same ``view_id``. That is not a flaw in the helper; it is the
    real shape of the hazard, because ``document_id`` never folds the text.

    So the assertion is not "different documents get different keys" — they do
    not. It is that the cache refuses to serve across the collision, which is the
    only thing standing between a colliding key and an offset map describing the
    wrong document.
    """
    first = _document("the first document, longer than the second\n")
    second = _document("short\n")

    one = MatchView.build(first)
    two = MatchView.build(second)

    assert one.view_id == two.view_id, (
        "if this ever fails the helper started folding text into the identity, "
        "and this test no longer exercises the collision it was written for"
    )
    assert one is not two
    assert one.text != two.text


# -- what it is not for -----------------------------------------------------


def test_the_cache_is_not_reached_when_the_grounding_artifact_is_reused() -> None:
    """FR-020's last clause, and the reason this cache is small.

    When the grounding artifact itself comes back from the store, ``ground()`` is
    never called, so no view is built and this cache is never consulted. The case
    it serves is the one artifact reuse cannot: several extractions grounding
    against **one** document inside a single process.
    """
    import tempfile
    from pathlib import Path

    pytest.importorskip("pymupdf")

    from docdoc.artifacts import FileArtifactStore
    from docdoc.extraction import SchemaRegistry
    from docdoc.extraction.adapters.echo import EchoAdapter
    from docdoc.pipeline import Stage, run

    store = FileArtifactStore(Path(tempfile.mkdtemp()))
    source = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()
    kwargs = {
        "schema": "invoice@1",
        "registry": SchemaRegistry.from_paths([Path("schemas")]),
        "adapter": EchoAdapter.from_fixtures("tests/fixtures/echo"),
        "store": store,
    }

    run(source, **kwargs)  # type: ignore[arg-type]

    clear_view_cache()
    second = run(source, **kwargs)  # type: ignore[arg-type]

    outcome = second.outcome_for(Stage.GROUND)
    assert outcome is not None
    assert outcome.status.value == "reused", "this asserts nothing unless grounding was reused"
    assert len(VIEWS) == 0, (
        "a view was built although the grounding artifact was reused, so the "
        "expensive half ran for a result that came from the store"
    )
