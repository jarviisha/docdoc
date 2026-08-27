/**
 * Failures, and the five paths a user can actually reach.
 *
 * SC-010 counts them: no schemas configured, document too large, unsupported
 * type, provider failure, mid-run failure. All five must name their cause and
 * none may leave an empty result — which is a stronger requirement than it
 * sounds, because a mid-run failure carries results Milestone 7 went to some
 * trouble to preserve.
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import {
  failureNotice,
  failureTitle,
  toFailureView,
  transportFailure,
} from "../src/model/failure.ts";
import { emptyRegistryNotice, toSchemaChoices } from "../src/model/schemas.ts";

/** The same run the other suites use, reshaped as a failure's survivors. */
const RUN = JSON.parse(
  readFileSync(new URL("./fixtures/run.json", import.meta.url), "utf8"),
) as { extraction: unknown; grounding: unknown };

/** The five reachable failures, as the API reports them. */
const REACHABLE = [
  { class: "UnsupportedDocumentError", stage: "parse", message: "media type not allowed" },
  { class: "UnsupportedDocumentError", stage: "parse", message: "document exceeds the size limit" },
  { class: "SchemaError", stage: "extract", message: "no schema named 'x' is registered" },
  { class: "ProviderError", stage: "extract", message: "the provider could not be reached" },
  { class: "ValidationError", stage: "validate", message: "a rule could not be evaluated" },
];

describe("every failure a user can reach", () => {
  it("names its cause", () => {
    for (const error of REACHABLE) {
      const notice = failureNotice(toFailureView({ error }));

      assert.match(notice, new RegExp(error.class));
      assert.match(notice, new RegExp(error.message.slice(0, 20)));
    }
  });

  it("names the stage it failed at", () => {
    for (const error of REACHABLE) {
      assert.match(failureNotice(toFailureView({ error })), new RegExp(error.stage));
    }
  });

  it("never produces an empty notice, even for a body that says nothing", () => {
    const notice = failureNotice(toFailureView({}));

    assert.ok(notice.length > 0);
    assert.match(notice, /UnknownError/);
  });
});

describe("a mid-run failure", () => {
  it("keeps the stages that completed", () => {
    // FR-025. A failed run has no job to fetch afterwards, so this response is
    // the only place those results will ever appear — discarding them here
    // wastes Milestone 7's FR-066 at the last possible step.
    const failure = toFailureView({
      error: { class: "ValidationError", stage: "validate", message: "rule failed" },
      outcomes: [
        { stage: "parse", status: "executed" },
        { stage: "extract", status: "executed" },
        { stage: "ground", status: "executed" },
        { stage: "validate", status: "failed" },
      ],
    });

    assert.equal(failure.completed.length, 3);
    assert.match(failureNotice(failure), /3 earlier stage/);
  });

  it("shows what the completed stages produced, not only that they ran", () => {
    // T091. The server sends `results` because a failed run has no job to fetch
    // afterwards, so that response is the only place those values will ever
    // appear. The model discarded the key entirely and the notice claimed they
    // were shown — a false sentence about the screen, in a viewer whose subject
    // is telling a reader what is really there.
    const failure = toFailureView({
      error: { class: "ValidationError", stage: "validate", message: "a rule could not run" },
      outcomes: [
        { stage: "parse", status: "executed" },
        { stage: "extract", status: "executed" },
        { stage: "ground", status: "executed" },
        { stage: "validate", status: "failed" },
      ],
      results: { extract: RUN.extraction, ground: RUN.grounding },
    });

    assert.notEqual(failure.survivors, null);
    assert.ok(
      (failure.survivors?.values.length ?? 0) > 0,
      "the surviving extraction's values must reach the view",
    );
  });

  it("lists the survivors with the same labels a completed run gets", () => {
    // One rendering path, not two. A failed run's values are values, and giving
    // them a presentation of their own is how the two come to disagree about
    // what a grounding status means.
    const failure = toFailureView({
      error: { class: "ValidationError", stage: "validate", message: "a rule could not run" },
      results: { extract: RUN.extraction, ground: RUN.grounding },
    });

    for (const row of failure.survivors?.values ?? []) {
      assert.ok(row.labels.status.length > 0, `${row.fieldPath} lost its status label`);
      assert.ok(row.labels.geometry.length > 0, `${row.fieldPath} lost its geometry label`);
    }
  });

  it("says results are shown only when there are results", () => {
    // The two halves of the sentence and the screen come from one value, so a
    // notice cannot claim something the view does not carry.
    const nothing = toFailureView({
      error: { class: "ParserError", stage: "parse", message: "the document would not parse" },
      outcomes: [{ stage: "parse", status: "failed" }],
    });

    assert.equal(nothing.survivors, null);
    assert.doesNotMatch(failureNotice(nothing), /are shown/);

    const something = toFailureView({
      error: { class: "ValidationError", stage: "validate", message: "a rule could not run" },
      outcomes: [{ stage: "extract", status: "executed" }],
      results: { extract: RUN.extraction, ground: RUN.grounding },
    });

    assert.match(failureNotice(something), /are shown/);
  });

  it("does not invent an empty result when extraction never ran", () => {
    // An omitted key and a key holding an empty result are different facts —
    // the same distinction FR-018 spends a union on, one layer up.
    const failure = toFailureView({
      error: { class: "ProviderError", stage: "extract", message: "the provider failed" },
      results: {},
    });

    assert.equal(failure.survivors, null);
  });

  it("carries docdoc's own message and no provider text", () => {
    const failure = toFailureView({
      error: { class: "ProviderError", stage: "extract", message: "the provider failed" },
    });

    assert.equal(failure.message, "the provider failed");
    assert.equal(failure.errorClass, "ProviderError");
  });
});

describe("a deployment with no schemas", () => {
  it("is told what to set, not that something is broken", () => {
    // FR-026, SC-010's first path. Milestone 7 fixed exactly this for the
    // command line: "the schema does not exist" sends a reader to the registry
    // by hand when what they need is the name of a setting.
    const notice = emptyRegistryNotice(toSchemaChoices({ schemas: [] }));

    assert.ok(notice);
    assert.match(notice ?? "", /DOCDOC_SCHEMA_PATHS/);
    assert.match(notice ?? "", /valid configuration/);
  });

  it("says nothing when there is something to choose", () => {
    const choices = toSchemaChoices({ schemas: [{ identity: "invoice@1" }] });

    assert.equal(emptyRegistryNotice(choices), null);
    assert.deepEqual(choices, [{ identity: "invoice@1" }]);
  });

  it("says nothing before the listing has arrived", () => {
    // T080, found by looking at the landing page. Before this, the caller held
    // an empty array from the first render, so the interface asserted that the
    // deployment had no schemas configured **before it had asked** — a claim
    // about the deployment that was false on every deployment with any.
    //
    // "Not yet known" and "known to be none" is the same distinction FR-018
    // keeps apart for geometry. Collapsing it here was that mistake elsewhere.
    assert.equal(emptyRegistryNotice(null), null);
  });
});

describe("the sixth failure path — the one the server never reports", () => {
  /**
   * SC-010 enumerates five failures and `REACHABLE` above covers them; every one
   * is the deployment telling us the run stopped. This is the other kind: the
   * run did not stop, we merely stopped hearing about it.
   *
   * The spec's Edge Cases are explicit — *"A proxy or a browser abandoning a
   * request does not abandon the run: the extraction continues, the provider is
   * still paid, and only the answer is lost. An interface that reports this as a
   * failed run is describing the connection, not the work."* It reported it as a
   * failed run, because the wording was built in a component's `.catch` where
   * this list could not reach it (T102).
   */
  it("is not called a failed run", () => {
    const lost = transportFailure(new Error("NetworkError: connection reset"));

    assert.equal(lost.origin, "transport");
    assert.doesNotMatch(failureTitle(lost), /the run failed/i);
    assert.doesNotMatch(failureNotice(lost), /the run failed/i);
  });

  it("says the three things only this case can say", () => {
    const notice = failureNotice(transportFailure(new Error("aborted")));

    // The run continues.
    assert.match(notice, /still running/i);
    // It has already been paid for.
    assert.match(notice, /cost is already incurred/i);
    // And the answer cannot be fetched later, because a storeless run has no
    // job identity (FR-003) — the fact nobody would guess, and the reason this
    // is not simply "try again".
    assert.match(notice, /no job identity/i);
    assert.match(notice, /second extraction at a second cost/i);
    // FR-050's obligation to the operator, at the moment it matters.
    assert.match(notice, /proxy/i);
  });

  it("claims nothing about what the run produced", () => {
    // Not "the run produced nothing" — we do not know what it produced.
    const lost = transportFailure(new Error("aborted"));

    assert.equal(lost.survivors, null);
    assert.deepEqual(lost.completed, []);
    assert.doesNotMatch(failureNotice(lost), /produced no result/i);
  });

  it("reads differently from a server-reported failure", () => {
    const reported = toFailureView({
      error: { class: "ProviderError", stage: "extract", message: "the provider failed" },
    });
    const lost = transportFailure(new Error("aborted"));

    assert.equal(reported.origin, "run");
    assert.notEqual(failureTitle(reported), failureTitle(lost));
    assert.notEqual(failureNotice(reported), failureNotice(lost));
  });

  it("keeps every server-reported failure on the run side", () => {
    for (const error of REACHABLE) {
      assert.equal(toFailureView({ error }).origin, "run");
      assert.match(failureTitle(toFailureView({ error })), /the run failed/i);
    }
  });
});
