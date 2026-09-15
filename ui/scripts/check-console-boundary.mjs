#!/usr/bin/env node
/**
 * T009 — the constitutional fence, and the result-content rule beside it.
 *
 * Constitution v1.9.0 permits an operations console and keeps a review platform
 * deferred, and ADR-0019 §2 says what the difference is in five nouns rather
 * than in a principle. The reason is stated there and worth repeating here: a
 * principle is argued at review time, and an identifier is grepped.
 *
 * Two rule sets, one script. Both answer the same question — *what may this
 * console not contain* — and a file per prohibition is how a guard directory
 * becomes something nobody runs.
 *
 * Takes an optional directory argument so `test/guards-can-fail.test.mjs` can
 * point it at a fixture. A guard whose scan path is hard-coded cannot be tested,
 * and an untested guard is an assertion about an assertion.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const DEFAULT_DIR = join(ROOT, "src", "console");

/**
 * ADR-0019 §2. Five nouns, and the identifiers each one actually arrives as.
 *
 * `capacity` and `balance` are in the workload row and are the two most likely
 * false positives on this list — both are ordinary words. They are kept because
 * the thing being prevented is a console that grows a per-person workload view,
 * and that view is never called `workload` in the commit that adds it.
 */
const REVIEW_PLATFORM = [
  { match: /\bassign(ee|ed|ment)?\b/i, what: "reviewer assignment", noun: "assignment" },
  { match: /\bqueue\b|\bworklist\b|\bwork_list\b|\binbox\b/i, what: "a review queue", noun: "queue" },
  { match: /\bworkload\b|\bcapacity\b|\bbalance\b/i, what: "workload", noun: "workload" },
  { match: /\breview_?state\b|\bunder_?review\b|\bin_?review\b/i, what: "a review state", noun: "review state" },
  {
    match: /\bdisposition\b|\bapprove[d]?\b|\brejected?\b|\bescalate[d]?\b/i,
    what: "a disposition",
    noun: "disposition",
  },
];

/**
 * FR-037 and SC-016: the console displays no part of a result.
 *
 * SC-016 promises "one check with no exception list" and this is it. Without
 * this rule set that sentence names nothing.
 *
 * **`value` and `outcomes` are deliberately absent.** `value` is the prop of
 * every controlled input this console has, and `outcomes` is what a delivery's
 * attempts are legitimately called. A guard that fails correct code is a guard
 * somebody switches off — and switching this one off would take the five nouns
 * with it, because they live in the same file.
 */
const RESULT_CONTENT = [
  { match: /\bclaimed_?text\b/i, what: "claimed text" },
  { match: /\bgrounding(_?score)?\b/i, what: "grounding" },
  { match: /\bfield_?path\b/i, what: "a field path" },
  { match: /\bfindings\b/i, what: "validation findings" },
  { match: /\/v1\/jobs\/[^"'`]*\/result/, what: "the result endpoint, addressed rather than linked" },
];

/** A mention inside a comment is documentation, not a use. */
const COMMENT = /^\s*(\*|\/\/|\/\*)/;

/**
 * The narrow exemption, and it is narrow on purpose.
 *
 * A link to a result is what FR-022a permits — the console offers a link and
 * renders nothing. Constructing that href touches the result path, so the line
 * that does it says so.
 */
const EXEMPTION = /console-boundary-exempt: link only/;
const EXEMPTION_LOOKBEHIND = 4;

async function walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return []; // no console sources yet
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

async function scan(dir, rules) {
  const found = [];
  for (const file of await walk(dir)) {
    const source = await readFile(file, "utf8");
    const lines = source.split("\n");
    lines.forEach((line, index) => {
      if (COMMENT.test(line)) return;
      const nearby = lines.slice(Math.max(0, index - EXEMPTION_LOOKBEHIND), index + 1);
      if (nearby.some((candidate) => EXEMPTION.test(candidate))) return;
      for (const rule of rules) {
        if (rule.match.test(line)) {
          found.push({ file: relative(ROOT, file), line: index + 1, what: rule.what });
        }
      }
    });
  }
  return found;
}

// `resolve` and not `join`: a test passes an absolute fixture path, and joining
// that onto the cwd produces a path that exists nowhere.
const target = process.argv[2] ? resolve(process.cwd(), process.argv[2]) : DEFAULT_DIR;

const platform = await scan(target, REVIEW_PLATFORM);
const content = await scan(target, RESULT_CONTENT);

if (platform.length > 0 || content.length > 0) {
  if (platform.length > 0) {
    console.error("this is a review platform, and constitution v1.9.0 permits a console (FR-002):\n");
    for (const violation of platform) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
    }
    console.error(
      "\nADR-0019 §2 names five nouns this console may not hold: reviewer assignment,\n" +
        "a review queue, workload, a review state, a disposition. The permitted surface\n" +
        "is about the person who runs the deployment, not the person who checks documents.",
    );
  }

  if (content.length > 0) {
    console.error("\nthe console displayed part of a result (FR-037, SC-016):\n");
    for (const violation of content) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
    }
    console.error(
      "\nThe console links to the result representation the API already serves and\n" +
        'renders no part of it. If the line only builds a link, mark it\n' +
        '"console-boundary-exempt: link only" and say why.',
    );
  }

  process.exit(1);
}

console.log("not a review platform: clean");
console.log("no part of a result is rendered: clean");
