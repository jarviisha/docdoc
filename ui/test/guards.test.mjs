/**
 * The guard on the guards.
 *
 * Every check here passes on an empty tree, and a check that has only ever been
 * observed passing on no input is not evidence of anything — the same argument
 * `tests/unit/test_base_install_excludes_evaluation_data.py` makes for its own
 * subject: "excluding a directory that does not exist proves nothing".
 *
 * So each guard is run twice: once against a planted violation, where it must
 * fail, and once against the real tree, where it must pass. Written in plain
 * JavaScript rather than TypeScript because it tests scripts rather than the
 * view model, and because it should keep working if the type-stripping story
 * changes.
 *
 * The read-only guard is absent from this file because it is absent from the
 * repository: it fenced the viewer, and the viewer was removed (ADR-0020). Its
 * no-persistence half survived as `check-no-persistence.mjs` and is exercised
 * below, because that half never was about the viewer — it is what keeps the
 * console's credential from being written down (specs/011 FR-007, SC-004).
 */

import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const PROBE_MODEL = join(ROOT, "src", "console", "model", "__guard_probe.ts");
const PROBE_COMPONENT = join(ROOT, "src", "console", "components", "__guard_probe.tsx");

const run = (script) =>
  spawnSync(process.execPath, [join(ROOT, "scripts", script)], { encoding: "utf8" });

const plant = (path, contents) => {
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, contents, "utf8");
};

after(() => {
  rmSync(PROBE_MODEL, { force: true });
  rmSync(PROBE_COMPONENT, { force: true });
});

describe("the view-model boundary guard", () => {
  it("passes on the real tree", () => {
    assert.equal(run("check-model-boundary.mjs").status, 0);
  });

  it("fails when the model imports a component", () => {
    plant(PROBE_MODEL, 'import { x } from "../components/Shell.tsx";\nexport const y = x;\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /must not depend on components/);
    rmSync(PROBE_MODEL, { force: true });
  });

  it("fails when the model imports something that renders", () => {
    plant(PROBE_MODEL, 'import { useState } from "react";\nexport const y = useState;\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /rendering concern/);
    rmSync(PROBE_MODEL, { force: true });
  });
  it("fails when the model imports a shadcn primitive", () => {
    plant(PROBE_MODEL, 'import { Button } from "@/components/ui/button";\nexport const y = Button;\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /rendering concern/);
    rmSync(PROBE_MODEL, { force: true });
  });

  it("fails when the model imports radix-ui", () => {
    plant(PROBE_MODEL, 'import { Dialog } from "radix-ui";\nexport const y = Dialog;\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /rendering concern/);
    rmSync(PROBE_MODEL, { force: true });
  });
});

describe("the network boundary", () => {
  it("fails when a component calls fetch directly", () => {
    // FR-041. The console reaches exactly the endpoints `ALLOWED` names, which
    // is a guarantee only while every request is constructed in one tested
    // place. A component did call `fetch` itself until convergence found it.
    plant(PROBE_COMPONENT, 'export const C = () => { void fetch("/v1/anything"); return null; };\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /fetch\(\)/);
    rmSync(PROBE_COMPONENT, { force: true });
  });

  it("allows a component to mention fetch in a comment", () => {
    plant(PROBE_COMPONENT, "// this component does not call fetch\nexport const C = () => null;\n");
    assert.equal(run("check-model-boundary.mjs").status, 0);
    rmSync(PROBE_COMPONENT, { force: true });
  });
});

describe("the no-persistence guard", () => {
  it("passes on the real tree", () => {
    assert.equal(run("check-no-persistence.mjs").status, 0);
  });

  it("fails on browser storage in a component", () => {
    // FR-007. The credential lives in the page's memory; a reload asking for it
    // again is the accepted cost, not a gap to close with storage.
    plant(PROBE_COMPONENT, 'export const C = () => { localStorage.setItem("k", "x"); };\n');
    const result = run("check-no-persistence.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /localStorage/);
    rmSync(PROBE_COMPONENT, { force: true });
  });

  it("fails on browser storage in the model too", () => {
    // Scoped to all of `src/`, not to components: a model file caching a run
    // would put it at rest just as effectively.
    plant(PROBE_MODEL, 'export const x = () => { indexedDB.open("runs"); };\n');
    const result = run("check-no-persistence.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /IndexedDB/);
    rmSync(PROBE_MODEL, { force: true });
  });

  it("fails on a cookie, which ADR-0019 §4 rejected by name", () => {
    plant(PROBE_MODEL, 'export const x = () => { document.cookie = "k=v"; };\n');
    const result = run("check-no-persistence.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /a cookie/);
    rmSync(PROBE_MODEL, { force: true });
  });
});

describe("the licence inventory", () => {
  it("covers every declared dependency", () => {
    const result = run("check-licenses.mjs");
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /all compatible with Apache-2\.0/);
  });
});
