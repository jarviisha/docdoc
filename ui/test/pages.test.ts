/**
 * Which pages render, and that every other one stays reachable.
 *
 * Two halves that are each easy to satisfy alone and break each other when they
 * are: bounding the up-front work is trivial if you never let anyone see the
 * rest, and reaching everything is trivial if you render everything. Both are
 * asserted together for that reason (FR-051, FR-052, FR-053, SC-016).
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import {
  initialRequests,
  reachablePages,
  rendered,
  requestPage,
  selectivityNotice,
} from "../src/model/pages.ts";
import { toRunView } from "../src/model/run.ts";
import type { WireRun } from "../src/model/types.ts";

const RUN = JSON.parse(
  readFileSync(new URL("./fixtures/run.json", import.meta.url), "utf8"),
) as WireRun;

describe("the up-front bound", () => {
  it("renders exactly the pages carrying located values", () => {
    const view = toRunView(RUN, 1);
    const named = new Set(
      Object.values(RUN.grounding?.outcomes ?? {}).flatMap((outcome) =>
        (outcome.geometry ?? []).map(([pageIndex]) => pageIndex),
      ),
    );

    assert.deepEqual(view.pagesToRender, [...named].sort((a, b) => a - b));
  });

  it("does not grow when the same result is paired with a much longer document", () => {
    // SC-016 — the count tracks the *result*, never the document. This is the
    // assertion that fails when somebody renders every page "for simplicity".
    const short = toRunView(RUN, 1);
    const long = toRunView(RUN, 1000);

    assert.deepEqual(long.pagesToRender, short.pagesToRender);
    assert.equal(long.pageCount, 1000);
  });

  it("asks for no page it was not told about", () => {
    const requests = initialRequests(toRunView(RUN, 500));

    assert.deepEqual([...requests.upFront], toRunView(RUN, 500).pagesToRender);
    assert.equal(requests.onDemand.length, 0);
  });
});

describe("reachability", () => {
  it("makes every page of the document reachable", () => {
    // FR-052. SC-016 claims 0% of the document is unreachable; this is that
    // claim, and it had no implementation at all until `/speckit-analyze` found
    // the gap.
    const view = toRunView(RUN, 40);

    assert.equal(reachablePages(view).length, 40);
    assert.deepEqual(reachablePages(view).slice(0, 3), [0, 1, 2]);
  });

  it("adds exactly one render request for a page the result never named", () => {
    const view = toRunView(RUN, 40);
    const before = initialRequests(view);
    const unnamed = reachablePages(view).find((page) => !before.upFront.includes(page));
    assert.notEqual(unnamed, undefined);

    const after = requestPage(before, unnamed as number);

    assert.equal(after.onDemand.length, 1);
    assert.deepEqual([...after.upFront], [...before.upFront], "the up-front set must not grow");
    assert.equal(rendered(after).length, rendered(before).length + 1);
  });

  it("is idempotent — asking twice renders once", () => {
    const view = toRunView(RUN, 40);
    const once = requestPage(initialRequests(view), 7);
    const twice = requestPage(once, 7);

    assert.deepEqual(rendered(twice), rendered(once));
  });

  it("does not re-request a page already rendered up front", () => {
    const view = toRunView(RUN, 40);
    const before = initialRequests(view);
    const alreadyShown = before.upFront[0] as number;

    const after = requestPage(before, alreadyShown);

    assert.equal(after.onDemand.length, 0);
  });
});

describe("saying so", () => {
  it("states that pages are shown selectively", () => {
    // FR-054 — a partial view taken for the whole document is a reader misled by
    // a viewer whose entire purpose is telling them what is really there.
    const notice = selectivityNotice(toRunView(RUN, 40));

    assert.ok(notice);
    assert.match(notice ?? "", /of 40 pages/);
  });

  it("says nothing when every page is already shown", () => {
    const view = toRunView(RUN, 1);

    assert.equal(selectivityNotice(view), null);
  });
});
