"""`_JsonLines` — the formatter that makes docdoc's events an audit trail.

**Untested, and it is the renderer for every operator-facing event.** A plain
formatter prints `record.getMessage()` and discards ``extra={"docdoc": …}``,
which is where `credential.operation` keeps its actor, operation, tenant and
identifier — so the failure mode is not a mangled line, it is an audit trail
that says `credential.operation` and nothing about which credential.

Two record shapes are handled because the project emits two: `runs/observe.py`
serialises its payload into the message, everything else passes an `extra`. Both
have to come out as one JSON object, or a log aggregator gets two schemas from
one service.

`configure_logging` is here too, for its third branch. The other two — "a
`docdoc` handler already exists" and "the root logger has one" — are reached by
every other test in the suite; the branch that actually installs a handler is
reached by none, because pytest attaches one to the root before anything runs.
"""

from __future__ import annotations

import json
import logging

import pytest

from docdoc.telemetry import ROOT_LOGGER, configure_logging
from docdoc.telemetry import _JsonLines as JsonLines


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="docdoc.runs",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for name, value in extra.items():
        setattr(record, name, value)
    return record


def _formatted(record: logging.LogRecord) -> dict:
    return json.loads(JsonLines().format(record))


# -- the two shapes ----------------------------------------------------------


def test_an_extra_payload_is_merged_into_the_line() -> None:
    """The shape `credential.operation` uses. Losing this loses the audit trail."""
    line = _formatted(
        _record(
            "credential.operation",
            docdoc={"operation": "revoke", "tenant_id": "acme", "credential_id": "c-1"},
        )
    )

    assert line["operation"] == "revoke"
    assert line["tenant_id"] == "acme"
    assert line["credential_id"] == "c-1"
    assert line["level"] == "INFO"
    assert line["logger"] == "docdoc.runs"


def test_a_message_that_is_already_json_is_not_double_encoded() -> None:
    """The shape `runs/observe.py` uses: it serialised its own payload.

    Re-encoding would put a JSON string inside a JSON field, which every
    aggregator would index as one opaque blob.
    """
    line = _formatted(_record(json.dumps({"event": "run.transition", "to": "succeeded"})))

    assert line["event"] == "run.transition"
    assert line["to"] == "succeeded"


def test_prose_becomes_a_message_field() -> None:
    """Anything else is a human sentence, and it still has to be one object."""
    assert _formatted(_record("worker stopped"))["message"] == "worker stopped"


def test_json_that_is_not_an_object_is_treated_as_prose() -> None:
    """`"[1, 2]"` parses and is not a payload. Spreading it would raise inside
    the formatter, which is the one place an exception must not happen."""
    assert _formatted(_record("[1, 2]"))["message"] == "[1, 2]"


def test_an_exception_carries_its_class_and_not_its_traceback() -> None:
    """The rule every observer here follows: a traceback quotes locals, and
    locals in this codebase hold document content."""
    try:
        raise KeyError("total")
    except KeyError:
        import sys

        record = _record("delivery.failed")
        record.exc_info = sys.exc_info()

    line = _formatted(record)

    assert line["error"] == "KeyError"
    assert "total" not in json.dumps(line)


def test_a_value_json_cannot_encode_does_not_break_the_line() -> None:
    """`default=str` rather than a raised `TypeError`: a formatter that throws
    loses the record it was formatting *and* every one behind it."""
    line = _formatted(_record("run.transition", docdoc={"at": object()}))

    assert "at" in line


# -- configure_logging -------------------------------------------------------


def test_configure_logging_installs_one_handler_when_nothing_else_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch no other test reaches, and the reason the worker logged nothing.

    Every `docdoc.*` record is INFO, `docdoc` had no handler, and it inherited
    `WARNING` from the root — so the process where `run.transition` happens
    emitted nothing an operator could see.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    root = logging.getLogger()
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(root, "handlers", [])
    previous = logger.level

    try:
        assert configure_logging() == "installed"
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JsonLines)
        assert logger.level == logging.INFO
        # **`propagate` is left alone.** The version that set it to `False`
        # defended against nothing — this branch runs only when the root has no
        # handlers — and permanently blinded `caplog` for every later test.
        assert logger.propagate is True

        # Idempotent: the second call finds the handler it installed.
        assert configure_logging() == "configured"
        assert len(logger.handlers) == 1
    finally:
        logger.setLevel(previous)


def test_configure_logging_leaves_a_deployments_root_handler_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "root" changes nothing at all, and that is load-bearing.

    An earlier version set the level in this branch. It leaked across tests —
    a suite that had built one app saw three events where one was expected —
    because a level set on a process-global logger outlives the call.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    monkeypatch.setattr(logger, "handlers", [])
    logger.setLevel(logging.WARNING)
    previous = logger.level

    try:
        # pytest's own root handler is what makes this the branch taken.
        assert configure_logging() == "root"
        assert logger.handlers == []
        assert logger.level == previous
    finally:
        logger.setLevel(logging.NOTSET)
