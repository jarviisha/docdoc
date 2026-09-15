/**
 * T014 — the new guards can actually fail.
 *
 * `tests/contract/test_no_review_platform.py` ends with a test called
 * `test_this_check_can_actually_fail`, and the reason it exists applies twice
 * over here: a guard nobody has seen fail is a guard nobody knows works, and
 * these two are the only thing standing between this milestone and the surface
 * the constitution defers.
 *
 * Each guard is run against a fixture directory written here. That is the whole
 * reason both accept a directory argument — a hard-coded scan path cannot be
 * tested, and an untested guard is an assertion about an assertion.
 */

import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const SCRIPTS = fileURLToPath(new URL("../scripts", import.meta.url));

/** Run a guard over a fixture. Returns the exit code and what it printed. */
async function guard(script, directory) {
  try {
    const { stdout } = await run(process.execPath, [join(SCRIPTS, script), directory]);
    return { code: 0, output: stdout };
  } catch (error) {
    return { code: error.code, output: `${error.stdout ?? ""}${error.stderr ?? ""}` };
  }
}

async function fixture(files) {
  const directory = await mkdtemp(join(tmpdir(), "docdoc-guard-"));
  for (const [name, contents] of Object.entries(files)) {
    const path = join(directory, name);
    await mkdir(join(path, ".."), { recursive: true });
    await writeFile(path, contents);
  }
  return directory;
}

test("the console boundary fails on a review queue", async () => {
  const directory = await fixture({
    "model/work.ts": "export const queue = [];\n",
  });
  const { code, output } = await guard("check-console-boundary.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /review queue/);
  await rm(directory, { recursive: true, force: true });
});

test("the console boundary fails on each of the five nouns", async () => {
  for (const [name, line] of [
    ["assignment", "export const assignee = null;"],
    ["queue", "export const inbox = [];"],
    ["workload", "export const workload = 0;"],
    ["review state", "export const reviewState = 'open';"],
    ["disposition", "export const disposition = 'approved';"],
  ]) {
    const directory = await fixture({ "model/thing.ts": `${line}\n` });
    const { code } = await guard("check-console-boundary.mjs", directory);
    assert.equal(code, 1, `${name} did not trip the guard`);
    await rm(directory, { recursive: true, force: true });
  }
});

test("the console boundary fails on rendered result content", async () => {
  const directory = await fixture({
    "components/Thing.tsx": "export const show = (v) => v.claimed_text;\n",
  });
  const { code, output } = await guard("check-console-boundary.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /claimed text/);
  await rm(directory, { recursive: true, force: true });
});

test("the console boundary passes a control prop and a delivery outcome", async () => {
  // The two identifiers deliberately left off the result-content list. A guard
  // that fails correct code is a guard somebody switches off, and this one
  // carries the five nouns too.
  const directory = await fixture({
    "components/Fine.tsx": "export const a = <input value={x} />;\nexport const b = attempt.outcomes;\n",
  });
  const { code } = await guard("check-console-boundary.mjs", directory);
  assert.equal(code, 0);
  await rm(directory, { recursive: true, force: true });
});

test("the a11y guard fails on a div with a click handler", async () => {
  const directory = await fixture({
    "Thing.tsx": '<div onClick={erase}>Erase</div>\n',
  });
  const { code, output } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /pointer/);
  await rm(directory, { recursive: true, force: true });
});

test("the a11y guard fails on an unnamed input", async () => {
  const directory = await fixture({
    "Thing.tsx": "<input type=\"text\" />\n",
  });
  const { code, output } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /accessible name/);
  await rm(directory, { recursive: true, force: true });
});

test("the a11y guard fails a status message with nowhere to be heard", async () => {
  // T076. The failure that actually happened: a whole console of Banners and
  // not one live region in it.
  const directory = await fixture({
    "Thing.tsx": '<Banner status="error" title="it broke" />\n',
  });
  const { code, output } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /nowhere to be heard/);
  await rm(directory, { recursive: true, force: true });
});

test("the a11y guard passes a status message inside a live region", async () => {
  const directory = await fixture({
    "Thing.tsx":
      '<div role="status" aria-live="polite">\n  <Banner status="error" title="it broke" />\n</div>\n',
  });
  const { code } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 0);
  await rm(directory, { recursive: true, force: true });
});

test("the a11y guard passes a labelled native control", async () => {
  const directory = await fixture({
    "Thing.tsx": '<input id="key" type="password" />\n<button onClick={go}>Go</button>\n',
  });
  const { code } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 0);
  await rm(directory, { recursive: true, force: true });
});
