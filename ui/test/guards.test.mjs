/**
 * The guard on the guards.
 *
 * `check-model-boundary.mjs` and `check-readonly.mjs` both pass on an empty tree,
 * which is the state this repository is in until Phase 3 lands. A check that has
 * only ever been observed passing on no input is not evidence of anything — the
 * same argument `tests/unit/test_base_install_excludes_evaluation_data.py` makes
 * for its own subject: "excluding a directory that does not exist proves nothing".
 *
 * So each guard is run twice here: once against a planted violation, where it
 * must fail, and once against the real tree, where it must pass. Written in
 * plain JavaScript rather than TypeScript because it tests scripts rather than
 * the view model, and because it should keep working if the type-stripping
 * story changes.
 */

import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const PROBE_MODEL = join(ROOT, "src", "model", "__guard_probe.ts");
const PROBE_COMPONENT = join(ROOT, "src", "components", "__guard_probe.tsx");

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
    plant(PROBE_MODEL, 'import { x } from "@components/Overlay";\nexport const y = x;\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /must not depend on components/);
    rmSync(PROBE_MODEL, { force: true });
  });

  it("fails when the model imports the renderer", () => {
    plant(PROBE_MODEL, 'import * as pdfjs from "pdfjs-dist";\nexport const y = pdfjs;\n');
    const result = run("check-model-boundary.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /rendering concern/);
    rmSync(PROBE_MODEL, { force: true });
  });
});

describe("the read-only guard", () => {
  it("passes on the real tree", () => {
    assert.equal(run("check-readonly.mjs").status, 0);
  });

  it("fails on a text input in a component", () => {
    plant(PROBE_COMPONENT, "export const C = () => <input value=\"x\" />;\n");
    const result = run("check-readonly.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /<input>/);
    rmSync(PROBE_COMPONENT, { force: true });
  });

  it("allows the file picker when the exemption is stated on the line", () => {
    // FR-013 needs one way for a document to enter the viewer, and the platform
    // offers exactly one. The exemption is narrow and must be visible where it
    // is used, which is what this asserts.
    plant(
      PROBE_COMPONENT,
      "export const C = () => (\n" +
        "  // read-only-exempt: file picker — reads a local file, writes nothing\n" +
        '  <input type="file" />\n' +
        ");\n",
    );
    assert.equal(run("check-readonly.mjs").status, 0);
    rmSync(PROBE_COMPONENT, { force: true });
  });
});

describe("the network boundary", () => {
  it("fails when a component calls fetch directly", () => {
    // T083. SC-013 claims no request this viewer makes writes anything, which is
    // a guarantee only while every request is constructed in one tested place.
    // A component did call `fetch` itself until convergence found it.
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
  it("fails on browser storage in a component", () => {
    // FR-032, T084. The document goes to the extraction endpoint and nowhere
    // else; losing the view on reload is the accepted cost of that.
    plant(PROBE_COMPONENT, 'export const C = () => { localStorage.setItem("d", "x"); };\n');
    const result = run("check-readonly.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /localStorage/);
    rmSync(PROBE_COMPONENT, { force: true });
  });

  it("fails on browser storage in the model too", () => {
    // Scoped to all of `src/`, not to components: a model file caching a result
    // would put the document at rest just as effectively.
    plant(PROBE_MODEL, 'export const x = () => { indexedDB.open("docs"); };\n');
    const result = run("check-readonly.mjs");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /IndexedDB/);
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
