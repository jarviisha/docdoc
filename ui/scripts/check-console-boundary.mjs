#!/usr/bin/env node
/**
 * T009 — the constitutional fence.
 *
 * Constitution v1.9.0 permits an operations console and keeps a review platform
 * deferred, and ADR-0019 §2 says what the difference is in five nouns rather
 * than in a principle. The reason is stated there and worth repeating here: a
 * principle is argued at review time, and an identifier is grepped.
 *
 * There used to be a second rule set here, `RESULT_CONTENT`, which failed the
 * build on any part of a result because `specs/011` FR-022a and FR-037 forbade
 * rendering one. ADR-0023 §4 lets the console render results, and §6 retires
 * that rule set with its link-only exemption: a console allowed to render a
 * result cannot be guarded by a script whose job was forbidding it. The five
 * nouns below are unchanged by that and keep running.
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

/** A mention inside a comment is documentation, not a use. */
const COMMENT = /^\s*(\*|\/\/|\/\*)/;

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
  process.exit(1);
}

console.log("not a review platform: clean");
