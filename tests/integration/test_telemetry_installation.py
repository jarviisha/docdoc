"""T131, T133, T134 — what `install_bridge()` does, and what it refuses to do.

**Three tasks in one file** because all three are about the same function taking
the same four decisions, and three files would each set up an observer slot and
an endpoint to ask one question about it.

Nothing here needs a collector. T132's leak check and the live-exporter path are
the `otel`-marked tests; these run offline with the extra absent, which is
themselves the point of T134: a deployment that configured no endpoint must not
require `docdoc[otel]` to exist.
"""

from __future__ import annotations

import importlib.util

import pytest

from docdoc import telemetry
from docdoc.pipeline import observe as pipeline_observe
from docdoc.runs import observe as runs_observe
from docdoc.runs.observe import install_bridge

OTEL_INSTALLED = importlib.util.find_spec("opentelemetry") is not None


def _build():
    """What a front end passes: a thunk that builds the real exporter.

    Constructed only after the slot check passes, which is what keeps
    `opentelemetry` unimported on a deployment that did not ask for it.
    """
    return telemetry.bridge(endpoint="http://localhost:4318/v1/traces")


@pytest.fixture(autouse=True)
def empty_slots():
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)
    yield
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)


# -- T134: unconfigured is a configuration, and it needs no extra -------------


def test_no_endpoint_installs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """SC-014, SC-021 — and it does not so much as look for the exporter."""
    assert install_bridge(_build, endpoint=None) == "unconfigured"
    assert install_bridge(_build, endpoint="") == "unconfigured"
    assert install_bridge(_build, endpoint="   ") == "unconfigured"

    assert runs_observe.observer() is None
    assert pipeline_observe.observer() is None


def test_the_module_imports_on_a_base_install() -> None:
    """`opentelemetry` is imported **inside** `bridge()` (R5).

    A module-scope import would make this package unimportable without the
    extra, and `api.app` and `runs.worker` import it unconditionally — so the
    whole service would refuse to start on a base install.
    """
    assert callable(telemetry.bridge)
    assert callable(telemetry.span_for)
    # And the decision to install is **not** here: it reads the `runs` observer
    # slot, and this package sits below `runs`. `lint-imports` is what said so.
    assert not hasattr(telemetry, "install")


@pytest.mark.skipif(OTEL_INSTALLED, reason="the extra is installed, so this cannot be observed")
def test_a_configured_endpoint_without_the_extra_is_reported_and_fatal_to_nothing() -> None:
    """An operator who set an endpoint asked for export.

    Answering that request with silence would be the worst outcome; answering it
    by refusing to boot would be nearly as bad. So it is reported, once, and the
    process carries on.
    """
    assert install_bridge(_build, endpoint="http://localhost:4318/v1/traces") == "unavailable"
    assert runs_observe.observer() is None


# -- T131: an occupied slot is left alone -------------------------------------


@pytest.mark.parametrize("occupy", ["pipeline", "runs"])
def test_an_occupied_slot_is_not_displaced(occupy: str, caplog: pytest.LogCaptureFixture) -> None:
    """R4 — and **either** slot being occupied is enough to decline.

    `pipeline/observe.py` documents the single slot as a decision: "A deployment
    that wants two can write a function that calls two." An exporter that called
    `set_observer` behind the operator's back would take that slot from whatever
    they had installed, so tracing starting to work would mean somebody else's
    observability stopping.

    Declining on *either* is deliberate. Installing into the empty one and not
    the full one would leave a deployment with half its events exported and no
    way to notice which half.
    """
    theirs: list[dict] = []

    def _mine(payload: dict) -> None:
        theirs.append(payload)

    module = pipeline_observe if occupy == "pipeline" else runs_observe
    module.set_observer(_mine)

    with caplog.at_level("INFO", logger="docdoc.runs"):
        outcome = install_bridge(_build, endpoint="http://localhost:4318/v1/traces")

    assert outcome == "occupied"
    assert module.observer() is _mine, "the deployment's own observer was displaced"
    assert any(
        "telemetry.not_installed" in record.getMessage()
        or getattr(record, "docdoc", {}).get("event") == "telemetry.not_installed"
        for record in caplog.records
    ), "and it said so, once"


# -- the fourth outcome, which nothing here asserted --------------------------
#
# Three of the four were covered from the day this landed. `installed` — the one
# an operator is actually asking for — was not, so nothing checked that both
# slots receive the same emitter or that the success is reported at all. That is
# the same shape as the `trace.get_tracer` defect: the failure paths were
# tested, and the working path was assumed.


def test_both_slots_receive_the_same_emitter(caplog: pytest.LogCaptureFixture) -> None:
    """One emitter in two slots, and it says so.

    Two would double every span the pipeline produced inside a run; installing
    into one would export half a deployment's events with no way to notice which
    half.
    """
    emitted: list[dict] = []

    with caplog.at_level("INFO", logger="docdoc.runs"):
        outcome = install_bridge(lambda: emitted.append, endpoint="http://localhost:4318/v1/traces")

    assert outcome == "installed"
    assert runs_observe.observer() is pipeline_observe.observer() is not None
    events = {getattr(record, "docdoc", {}).get("event") for record in caplog.records}
    assert "telemetry.installed" in events


def test_a_builder_that_cannot_import_its_exporter_is_reported_and_fatal_to_nothing() -> None:
    """The `unavailable` outcome, without needing the extra to be absent.

    The test above it can only run on a base install, so on every job that
    installs `docdoc[otel]` — including the one that measures coverage — this
    branch was never executed. A builder that raises is what a missing exporter
    looks like from here, and the slots must be left empty.
    """

    def _cannot():
        raise ImportError("no module named 'opentelemetry'")

    assert install_bridge(_cannot, endpoint="http://localhost:4318/v1/traces") == "unavailable"
    assert runs_observe.observer() is None
    assert pipeline_observe.observer() is None


def test_the_log_says_which_of_the_four_happened(caplog: pytest.LogCaptureFixture) -> None:
    """Each outcome is something an operator would otherwise infer from an
    absence of traces, which is the hardest thing to debug there is."""
    pipeline_observe.set_observer(lambda _: None)

    with caplog.at_level("INFO", logger="docdoc.runs"):
        install_bridge(_build, endpoint="http://localhost:4318/v1/traces")

    events = {getattr(record, "docdoc", {}).get("event") for record in caplog.records}
    assert "telemetry.not_installed" in events


# -- the header form ----------------------------------------------------------


def test_headers_parse_in_the_form_every_otlp_tool_uses() -> None:
    assert telemetry.parse_headers("a=1,b=2") == {"a": "1", "b": "2"}
    assert telemetry.parse_headers(" authorization = Bearer x ") == {"authorization": "Bearer x"}
    assert telemetry.parse_headers("") == {}
    assert telemetry.parse_headers(None) == {}


def test_a_malformed_header_is_dropped_rather_than_fatal() -> None:
    """Read at startup. A service that refuses to boot over a stray comma has
    turned an observability misconfiguration into an outage."""
    assert telemetry.parse_headers("a=1,,garbage,b=2") == {"a": "1", "b": "2"}
