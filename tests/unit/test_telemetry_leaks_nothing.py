"""T132, SC-015 — nothing seeded into an event reaches a span attribute.

The same technique Milestone 9 used for run records: seed distinctive strings
into every field an event could carry and require zero occurrences in what
leaves. Spans go to somebody else's server, so this is the observer with the
largest blast radius and it gets the same treatment the others get.

**Offline, and that is why `span_for` is a function rather than a closure inside
`bridge`.** A version of this test that needed a collector would run on nobody's
laptop and in one CI job, which is not where a leak gets caught.
"""

from __future__ import annotations

import json

from docdoc.telemetry import _STAGE_ATTRIBUTES, _TRANSITION_ATTRIBUTES, span_for

#: Strings that could only appear by having been copied out of a document, a
#: provider's answer, a prompt, or a credential.
SEEDED = (
    "ACME-INVOICE-CONFIDENTIAL",
    "Total due: 1,234.56",
    "ddk_live_notarealkeybutlookslikeone",
    "You are a helpful assistant. Extract the following",
    "the model refused because the document said",
)


def _stage_event(**extra: object) -> dict:
    payload = {
        "event": "pipeline.stage",
        "request_id": "req-1",
        "processing_id": "sha256:" + "b" * 64,
        "step_id": "validate",
        "artifact_id": "sha256:" + "a" * 64,
        "outcome": "executed",
        "reused": False,
        "duration_ms": 7,
    }
    payload.update(extra)
    return payload


def _transition_event(**extra: object) -> dict:
    payload = {
        "event": "run.transition",
        "run_id": "0f8b1c2d-3e4f-4a5b-8c9d-0e1f2a3b4c5d",
        "tenant_id": "acme",
        "from_state": "running",
        "to_state": "succeeded",
        "attempts": 1,
        "worker_id": "host:1",
        "reason": "completed",
    }
    payload.update(extra)
    return payload


def test_a_stage_span_carries_identifiers_counts_and_class_names() -> None:
    name, attributes = span_for(_stage_event())

    assert name == "stage.validate"
    assert attributes["artifact_id"].startswith("sha256:")
    assert attributes["duration_ms"] == 7


def test_a_transition_span_carries_the_states() -> None:
    name, attributes = span_for(_transition_event())

    assert name == "run.succeeded"
    assert attributes["from_state"] == "running"
    assert attributes["to_state"] == "succeeded"


def test_nothing_seeded_into_an_event_reaches_a_span() -> None:
    """**The requirement.** Every seeded string in every field, and none survives.

    The seeds go into fields that are *not* on the allow-list, which is the
    realistic failure: a future emission point adds `prompt` or `value` and an
    exporter that dumped the payload wholesale would carry it out of the
    deployment without anybody deciding it should.
    """
    contaminated = {
        "document_text": SEEDED[0],
        "value": SEEDED[1],
        "credential": SEEDED[2],
        "prompt": SEEDED[3],
        "provider_message": SEEDED[4],
        # And the worst case: a seed in a field that *is* exported, which is the
        # upstream's bug rather than this module's — but a token count and a
        # class name are the only free-ish fields, and neither is text.
        "usage": {"input_tokens": 10},
    }

    for event in (_stage_event(**contaminated), _transition_event(**contaminated)):
        result = span_for(event)
        assert result is not None
        body = json.dumps(result[1])
        for seeded in SEEDED:
            assert seeded not in body, f"{seeded!r} left the deployment as a span attribute"
        assert "usage" not in result[1], (
            "a token count is an enforcement counter, and a tracing backend is "
            "not where an invoice starts"
        )


def test_an_unknown_event_is_dropped_rather_than_exported_generically() -> None:
    """The rule that makes the assertion above hold for events not yet written."""
    assert span_for({"event": "retention.swept", "tenant_id": "acme", "runs": 4}) is None
    assert span_for({"event": "credential.operation", "credential_id": "x"}) is None
    assert span_for({}) is None


def test_the_allow_lists_name_no_field_that_could_carry_content() -> None:
    """The policy, read as a list, because it *is* the policy.

    A field added to either tuple is a decision to export it, and this is where
    that decision has to be made in the open.
    """
    forbidden = {"usage", "value", "text", "prompt", "message", "body", "credential"}

    for allowed in (_STAGE_ATTRIBUTES, _TRANSITION_ATTRIBUTES):
        assert not (set(allowed) & forbidden)
        assert not any(name.endswith("_text") or name.endswith("_body") for name in allowed)


def test_the_seed_check_can_actually_fail() -> None:
    """Guards the guard: an allow-list that emptied would pass vacuously."""
    assert span_for(_stage_event())[1], "a stage span carries something"
    assert span_for(_transition_event())[1], "a transition span carries something"
