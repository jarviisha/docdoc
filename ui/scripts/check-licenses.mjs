#!/usr/bin/env node
/**
 * T071 — no dependency imposes an obligation incompatible with Apache-2.0
 * (FR-039, SC-014).
 *
 * Two failures are possible and both are caught here. A dependency added to
 * `package.json` and never recorded in LICENSES.md is the common one: the file
 * stays accurate about what it lists and silent about what it does not. A
 * dependency recorded with a copyleft licence is the rare one, and the reason
 * the denylist is blunt rather than advisory.
 *
 * Reads the manifest and the inventory; contacts no registry, so it runs
 * offline and in CI without a network step.
 */

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = new URL("..", import.meta.url);

/**
 * Incompatible with shipping inside an Apache-2.0 distribution. PyMuPDF is the
 * live example on the Python side -- AGPL-3.0, quarantined behind an opt-in
 * extra -- and the whole reason the browser renderer was chosen on licence
 * before it was chosen on merit.
 */
const DENIED = [/AGPL/i, /(^|[^L])GPL-[23]/i, /SSPL/i, /BUSL/i, /UNLICENSED/i, /proprietary/i];

const manifest = JSON.parse(await readFile(new URL("package.json", ROOT), "utf8"));
const inventory = await readFile(new URL("LICENSES.md", ROOT), "utf8");

const declared = [
  ...Object.keys(manifest.dependencies ?? {}),
  ...Object.keys(manifest.devDependencies ?? {}),
];

/** Rows look like: | `name` | version | Licence | yes | */
const rows = new Map();
for (const line of inventory.split("\n")) {
  const match = line.match(/^\|\s*`([^`]+)`[^|]*\|([^|]*)\|([^|]*)\|/);
  if (match) rows.set(match[1].trim(), match[3].trim());
}

const problems = [];

for (const name of declared) {
  if (!rows.has(name)) {
    problems.push(`${name} is in package.json and not in LICENSES.md — record it with its licence`);
    continue;
  }
  const licence = rows.get(name);
  if (DENIED.some((pattern) => pattern.test(licence))) {
    problems.push(`${name} is ${licence}, which docdoc cannot ship inside an Apache-2.0 distribution`);
  }
}

for (const name of rows.keys()) {
  if (!declared.includes(name)) {
    problems.push(`${name} is in LICENSES.md and not in package.json — the inventory is stale`);
  }
}

if (problems.length > 0) {
  console.error("licence check failed (FR-039):\n");
  for (const problem of problems) console.error(`  ${problem}`);
  console.error(`\nInventory: ${fileURLToPath(new URL("LICENSES.md", ROOT))}`);
  process.exit(1);
}

console.log(`licences: ${declared.length} dependencies, all compatible with Apache-2.0`);
