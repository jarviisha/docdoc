#!/usr/bin/env node
/**
 * Nothing in this application comes to rest in the browser (specs/011 FR-007).
 *
 * This was the second half of `check-readonly.mjs`, whose first half — no
 * `<input>`, no `<form>`, no `onSubmit` under `src/components/` — was the
 * viewer's read-only fence and went with the viewer (ADR-0020). Splitting rather
 * than deleting, because the two halves guarded two different claims and only
 * one of them was about the viewer:
 *
 *   the fence that died    the viewer renders and never edits (specs/008 FR-029)
 *   the fence that did not the credential is never written down (specs/011 FR-007)
 *
 * The surviving one now guards the more valuable thing. The console holds an API
 * key in memory for the life of a page; FR-007 says it may never be persisted,
 * and a reload asking for it again is a designed consequence rather than a gap.
 * SC-004 claims zero recoverable bytes from any browser storage mechanism, and
 * this is what makes that a check instead of an intention.
 *
 * It has to be a source check rather than a test: the rendering layer carries no
 * automated test, so a `localStorage.setItem` added to a component would be
 * caught by nothing else.
 *
 * Takes an optional directory argument so the guard itself can be tested against
 * a fixture — a hard-coded scan path cannot be, and an untested guard is an
 * assertion about an assertion.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

/**
 * All of `src/`, models included.
 *
 * Not components alone: a model file caching a run would put it at rest just as
 * effectively, and the requirement is about the application rather than about
 * one directory.
 */
const DEFAULT_DIR = join(ROOT, "src");

/** Every mechanism SC-004 names. */
const NO_PERSISTENCE = [
  { match: /\blocalStorage\b/, what: "localStorage" },
  { match: /\bsessionStorage\b/, what: "sessionStorage" },
  { match: /\bindexedDB\b/i, what: "IndexedDB" },
  { match: /\bcaches\s*\./, what: "the Cache API" },
  { match: /\bdocument\s*\.\s*cookie\b/, what: "a cookie" },
];

/** A mention inside a comment is documentation, not a use. */
const COMMENT = /^\s*(\*|\/\/|\/\*)/;

async function walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return [];
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

const target = process.argv[2] ? resolve(process.cwd(), process.argv[2]) : DEFAULT_DIR;

const found = [];
for (const file of await walk(target)) {
  const source = await readFile(file, "utf8");
  source.split("\n").forEach((line, index) => {
    if (COMMENT.test(line)) return;
    for (const rule of NO_PERSISTENCE) {
      if (rule.match.test(line)) {
        found.push({ file: relative(ROOT, file), line: index + 1, what: rule.what });
      }
    }
  });
}

if (found.length > 0) {
  console.error("something was put at rest in the browser (specs/011 FR-007):\n");
  for (const violation of found) {
    console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
  }
  console.error(
    "\nThe credential lives in the page's memory and nowhere else. A reload asking\n" +
      "for it again is the accepted cost of that, recorded in the spec and in\n" +
      "ADR-0019 §4 — not a problem to solve with storage.",
  );
  process.exit(1);
}

console.log("nothing comes to rest in the browser: clean");
