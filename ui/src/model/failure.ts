/**
 * A failed run, and the results that survived it.
 *
 * Milestone 7's FR-066 preserves the completed stages' results on a mid-run
 * failure precisely so a surface like this can show them — a failed run has no
 * job to fetch afterwards, so that response is the only place they will ever
 * appear. Rendering "error" and discarding them wastes the guarantee at the last
 * possible step (FR-025).
 *
 * **Until T091 that is exactly what happened.** `WireError` did not declare the
 * `results` key the server sends, `findings` was hard-coded to `[]`, and the
 * only thing a failure put on screen was a sentence — which then claimed the
 * completed stages' results "are shown". They were not. The claim is the part
 * worth recording: a viewer whose whole subject is telling a reader what is
 * really there had a message asserting the presence of something it had
 * discarded one function earlier.
 *
 * The survivors are turned into an ordinary `RunView` by `toRunView`, so a
 * partial result is listed, labelled and drawn by the same tested code as a
 * complete one. There is deliberately no second rendering path: a failed run's
 * values are values, and giving them their own presentation is how the two
 * would come to disagree about what a grounding status means.
 */

import { toRunView } from "./run.ts";
import type { RunView, WireRun } from "./types.ts";

/** One stage the run got through, as the server reported it. */
export interface StageResult {
  stage: string;
  status: string;
}

/**
 * Which thing failed — and they are not the same thing (T102).
 *
 * `run` is the deployment reporting that the extraction stopped. `transport` is
 * this browser losing the answer: the request was abandoned by a proxy, the
 * network went, or the body came back unreadable. The spec's Edge Cases are
 * explicit that the second must not be reported as the first — *"A proxy or a
 * browser abandoning a request does not abandon the run: the extraction
 * continues, the provider is still paid, and only the answer is lost. An
 * interface that reports this as a failed run is describing the connection, not
 * the work."*
 *
 * The interface reported it as a failed run, under a banner reading "The run
 * failed", because the wording was assembled in a component's `.catch` where no
 * test could see it and no contract listed it. `docs/concepts/viewer.md` already
 * warned operators from the other side that a proxy killing a request "will look
 * like a viewer bug" — it looked like one because the viewer said so.
 */
export type FailureOrigin = "run" | "transport";

export interface FailureView {
  origin: FailureOrigin;
  stage: string | null;
  errorClass: string;
  message: string;
  /** Stage outcomes the run got through before it stopped. */
  completed: StageResult[];
  /**
   * What those stages actually produced, or `null` if the run stopped before
   * anything survived. This is the half FR-025 asks for that the outcome list
   * above cannot supply: "extract executed" is not the extracted values.
   *
   * Always `null` for a `transport` failure: the run may well have produced
   * everything, and we simply never received it.
   */
  survivors: RunView | null;
}

interface WireError {
  error?: { class?: string; stage?: string | null; message?: string };
  outcomes?: { stage: string; status: string }[];
  /**
   * Keyed by stage name, as `docdoc.api.errors._surviving` writes it. The names
   * are the pipeline's (`extract`, `ground`, `validate`) and not the run
   * response's (`extraction`, `grounding`, `validation`) — one of the two
   * vocabularies has to be translated, and doing it here keeps `toRunView`
   * taking exactly one shape.
   */
  results?: {
    extract?: unknown;
    ground?: unknown;
    validate?: unknown;
  };
}

/**
 * The surviving stages as a run, or `null` when nothing survived.
 *
 * An omitted key and a key holding an empty result are different facts — the
 * server's `_surviving` says so in its own docstring — so a missing `extract`
 * yields `null` here rather than an empty `RunView`. A viewer that showed an
 * empty list for "extraction never ran" would be making the claim FR-018 spends
 * a whole union preventing, one layer up.
 */
function toSurvivors(results: WireError["results"], pageCount: number): RunView | null {
  if (results === undefined) return null;
  if (results.extract === undefined || results.extract === null) return null;

  const run = {
    document_id: null,
    schema_identity: "",
    verdict: null,
    extraction: results.extract,
    grounding: results.ground ?? null,
    validation: results.validate ?? null,
  } as unknown as WireRun;

  return toRunView(run, pageCount);
}

export function toFailureView(body: WireError, pageCount = 1): FailureView {
  const error = body.error ?? {};
  return {
    origin: "run",
    stage: error.stage ?? null,
    errorClass: error.class ?? "UnknownError",
    // docdoc's own message, never a provider's — a provider's may quote the
    // document it choked on, which is the reason the API never forwards one.
    message: error.message ?? "the run failed and said nothing about why",
    completed: (body.outcomes ?? []).filter((outcome) => outcome.status !== "failed"),
    survivors: toSurvivors(body.results, pageCount),
  };
}

/**
 * The answer was lost. The run was not (FR-050, spec §Edge Cases).
 *
 * Three facts belong here and nowhere else, because only this case has them,
 * and an operator who is not told them will read the banner as a defect:
 *
 *  1. **The extraction is still running.** Closing a connection does not close a
 *     run; the server has no idea the browser left.
 *  2. **It has already been paid for.** The provider tokens are spent whether or
 *     not anyone sees the result, which is the same reason FR-047 forbids a
 *     control that claims to cancel.
 *  3. **The answer cannot be fetched later.** A storeless run writes no terminal
 *     artifact and therefore has no job identity (FR-003), so this is genuinely
 *     lost rather than merely delayed — a caller who needs a retrievable
 *     identity submits the document first and uses the store-backed route.
 *
 * The third is the one nobody would guess, and it is the reason this is not
 * simply "try again": trying again is a second extraction at a second cost.
 */
export function transportFailure(cause: unknown): FailureView {
  return {
    origin: "transport",
    stage: null,
    errorClass: "ConnectionLost",
    message: String(cause),
    completed: [],
    // Not "the run produced nothing" — we do not know what it produced.
    survivors: null,
  };
}

/**
 * What to call the failure on screen.
 *
 * A title is a claim about what went wrong, so it is decided here rather than
 * written into a component's JSX (FR-043). "The run failed" over a lost
 * connection is the specific false claim T102 exists to remove.
 */
export function failureTitle(failure: FailureView): string {
  return failure.origin === "transport"
    ? "The connection was lost — the run was not"
    : "The run failed";
}

/**
 * The sentence shown for a failure a user can reach (SC-010).
 *
 * Produced here rather than in a component so that "100% of failure paths name
 * their cause" is a property of a function rather than of a screen nobody tests.
 *
 * **It says results are shown only when they are.** The sentence and the screen
 * are produced from the same `survivors` value, so they cannot disagree — which
 * is the specific way this function was wrong before T091, and a wrong sentence
 * about what is on screen is worse than no sentence at all.
 */
export function failureNotice(failure: FailureView): string {
  if (failure.origin === "transport") {
    // Deliberately says nothing about the run having failed, because it has not,
    // and nothing about retrying being free, because it is not.
    return (
      "The answer did not reach this page, but the extraction is still running on " +
      "the server and its provider cost is already incurred. A storeless run keeps " +
      "no job identity, so this particular answer cannot be fetched later — running " +
      "it again is a second extraction at a second cost. If a proxy sits in front of " +
      "this deployment, it must allow a request to last as long as the slowest " +
      `expected extraction. (${failure.message})`
    );
  }

  const where = failure.stage === null ? "" : ` at the ${failure.stage} stage`;

  let kept = "";
  if (failure.survivors !== null) {
    const values = failure.survivors.values.length;
    kept =
      ` ${failure.completed.length} earlier stage(s) completed; ` +
      `the ${values} value(s) they produced are shown below.`;
  } else if (failure.completed.length > 0) {
    kept = ` ${failure.completed.length} earlier stage(s) completed, but produced no result to show.`;
  }

  return `${failure.errorClass}${where}: ${failure.message}.${kept}`;
}
