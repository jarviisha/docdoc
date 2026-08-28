#!/usr/bin/env node
/**
 * T006 — the rendering layer offers nothing that edits (FR-029).
 *
 * The viewer is read-only in the strong sense: there is no control it can show
 * that changes any stored byte. That is not a nicety — it is the fence around
 * the spec's argument that this milestone is not the "full review UI" the
 * constitution defers, and an argument nobody checks is an argument that stops
 * being true.
 *
 * It has to be a source check rather than a test because of the same decision
 * that makes it necessary: the rendering layer carries no automated test, so a
 * text input added to a component would be caught by nothing. Reading the source
 * is the only inspection available, so it is the one that runs.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const COMPONENTS_DIR = join(ROOT, "src", "components");
const SRC_DIR = join(ROOT, "src");

/** Controls that edit. Only components can render one (FR-029). */
const NO_EDITING = [
  { match: /<input\b/i, what: "<input>" },
  { match: /<textarea\b/i, what: "<textarea>" },
  { match: /<form\b/i, what: "<form>" },
  { match: /\bcontentEditable\b/, what: "contentEditable" },
  { match: /\bonSubmit\s*=/, what: "onSubmit handler" },
];

/**
 * Places a document could come to rest in the browser (FR-032), added by T084.
 *
 * Checked across the whole of `src/` rather than components alone: a model file
 * caching a result would put the document at rest just as effectively, and the
 * requirement is about the application, not about one directory.
 *
 * The accepted consequence of having none of these is that a reload loses the
 * view and the user picks the file again. Anything added here to "fix" that has
 * done the thing FR-032 forbids.
 */
const NO_PERSISTENCE = [
  { match: /\blocalStorage\b/, what: "localStorage" },
  { match: /\bsessionStorage\b/, what: "sessionStorage" },
  { match: /\bindexedDB\b/i, what: "IndexedDB" },
  { match: /\bcaches\s*\./, what: "the Cache API" },
];

/** A mention inside a comment is documentation, not a use. */
const COMMENT = /^\s*(\*|\/\/|\/\*)/;

/**
 * The one exception, and it is narrow enough to state: choosing a local file is
 * how a document enters the viewer at all (FR-013), and the platform's only
 * affordance for that is `<input type="file">`. It edits nothing — it reads a
 * file the user selected and hands the bytes to an extraction the server does
 * not persist. A line claiming this exemption must say so, so the exemption is
 * always visible at the point it is used rather than buried here.
 */
const EXEMPTION = /read-only-exempt: file picker/;

async function walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return []; // no components yet
    throw error;
  }
  const files = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(path)));
    else if (/\.(ts|tsx|jsx|js)$/.test(entry.name)) files.push(path);
  }
  return files;
}

/**
 * How far back the exemption may sit.
 *
 * Four lines, because a justification worth reading rarely fits on one and the
 * first draft of this check accepted only the immediately preceding line — which
 * rejected a perfectly well-marked file and would have pushed the next author to
 * cramp the reason rather than state it. Still bounded: an exemption that has
 * drifted five lines from what it exempts is no longer visible at the point of
 * use, which is the whole property being bought.
 */
const EXEMPTION_LOOKBEHIND = 4;

async function scan(dir, rules, { allowExemption }) {
  const found = [];
  for (const file of await walk(dir)) {
    const source = await readFile(file, "utf8");
    const lines = source.split("\n");
    lines.forEach((line, index) => {
      if (COMMENT.test(line)) return;
      if (allowExemption) {
        const nearby = lines.slice(Math.max(0, index - EXEMPTION_LOOKBEHIND), index + 1);
        if (nearby.some((candidate) => EXEMPTION.test(candidate))) return;
      }
      for (const rule of rules) {
        if (rule.match.test(line)) {
          found.push({ file: relative(ROOT, file), line: index + 1, what: rule.what });
        }
      }
    });
  }
  return found;
}

const editing = await scan(COMPONENTS_DIR, NO_EDITING, { allowExemption: true });
const persistence = await scan(SRC_DIR, NO_PERSISTENCE, { allowExemption: false });

if (editing.length > 0 || persistence.length > 0) {
  if (editing.length > 0) {
    console.error("read-only guarantee violated (FR-029):\n");
    for (const violation of editing) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
    }
    console.error(
      "\nThis viewer displays results and never edits them. If a file picker is what\n" +
        'you need, mark the line "read-only-exempt: file picker" and say why.',
    );
  }

  if (persistence.length > 0) {
    console.error("\nthe document was put at rest in the browser (FR-032):\n");
    for (const violation of persistence) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
    }
    console.error(
      "\nThe document goes to the extraction endpoint and nowhere else. Losing the\n" +
        "view on reload is the accepted cost of that, recorded in the spec — not a\n" +
        "problem to solve with storage.",
    );
  }

  process.exit(1);
}

console.log("read-only guarantee: clean");
console.log("nothing puts the document at rest in the browser: clean");
