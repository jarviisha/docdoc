#!/usr/bin/env node
/**
 * The view model imports nothing that can render (specs/008 FR-043, R9).
 *
 * Principle XII requires dependency boundaries to be enforced by an automated
 * test rather than by convention, and this repository already holds the Python
 * layer graph to that standard with import-linter. The browser client gets the
 * same treatment for a sharper reason: the rendering layer carries no automated
 * test at all, so the model is the entire tested surface. A decision that drifts
 * into a component is a requirement that silently loses its coverage.
 *
 * Deliberately dependency-free: it reads files and matches import specifiers.
 * A parser would be more precise and would cost a dependency that R4 spent an
 * argument avoiding, on a rule with no exceptions to be precise about.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
// One model and one component tree since the viewer was removed (ADR-0020).
// The rules did not change with it, because the property did not — and the
// console has more to lose than the viewer did, since it sends a credential and
// a second `fetch` site is a second place that can fail to.
const MODEL_DIRS = [join(ROOT, "src", "console", "model")];
const COMPONENT_DIRS = [join(ROOT, "src", "console", "components"), join(ROOT, "src", "components")];

/**
 * Components reach the network through `src/transport.ts` or not at all.
 *
 * specs/011 FR-041 requires every request to be built at the one tested
 * construction point, and that is a guarantee only while nothing else calls
 * `fetch`. It did not hold once: a component was assembling the URL and calling
 * `fetch` itself, so the claim rested on nobody adding a second call site. This
 * is what makes the single call site a rule.
 */
const NETWORK_CALLS = [
  { match: /\bfetch\s*\(/, what: "fetch()" },
  { match: /\bXMLHttpRequest\b/, what: "XMLHttpRequest" },
  { match: /\bnavigator\.sendBeacon\b/, what: "navigator.sendBeacon" },
  { match: /\bnew\s+EventSource\b/, what: "EventSource" },
  { match: /\bnew\s+WebSocket\b/, what: "WebSocket" },
];

/** Anything that renders, or that leads to something that renders. */
const FORBIDDEN = [
  { match: /^react(\/|$)/, why: "React is a rendering concern" },
  { match: /^react-dom(\/|$)/, why: "React DOM is a rendering concern" },
  { match: /^@astryxdesign\//, why: "component libraries are a rendering concern" },
  { match: /^@\/components\//, why: "component libraries are a rendering concern" },
  { match: /^radix-ui(\/|$)/, why: "component libraries are a rendering concern" },
  { match: /^@radix-ui\//, why: "component libraries are a rendering concern" },
  { match: /^lucide-react(\/|$)/, why: "component libraries are a rendering concern" },
  { match: /^@components\//, why: "the model must not depend on components" },
  { match: /(^|\/)\.\.\/components\//, why: "the model must not depend on components" },
];

const IMPORT_RE =
  /(?:^|\n)\s*import\s[^'"]*from\s*['"]([^'"]+)['"]|(?:^|\n)\s*export\s[^'"]*from\s*['"]([^'"]+)['"]|\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)|\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g;

async function walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return []; // the model does not exist yet
    throw error;
  }
  const files = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(path)));
    else if (/\.(ts|tsx|mts|js|mjs)$/.test(entry.name)) files.push(path);
  }
  return files;
}

async function walkAll(dirs) {
  const files = [];
  for (const dir of dirs) files.push(...(await walk(dir)));
  return files;
}

const violations = [];
for (const file of await walkAll(MODEL_DIRS)) {
  const source = await readFile(file, "utf8");
  for (const match of source.matchAll(IMPORT_RE)) {
    const specifier = match[1] ?? match[2] ?? match[3] ?? match[4];
    if (!specifier) continue;
    const rule = FORBIDDEN.find((candidate) => candidate.match.test(specifier));
    if (rule) {
      violations.push({ file: relative(ROOT, file), specifier, why: rule.why });
    }
  }
}

const networkViolations = [];
for (const file of await walkAll(COMPONENT_DIRS)) {
  const source = await readFile(file, "utf8");
  source.split("\n").forEach((line, index) => {
    // A mention inside a comment is documentation, not a call site.
    if (/^\s*(\*|\/\/)/.test(line)) return;
    for (const rule of NETWORK_CALLS) {
      if (rule.match.test(line)) {
        networkViolations.push({ file: relative(ROOT, file), line: index + 1, what: rule.what });
      }
    }
  });
}

if (violations.length > 0 || networkViolations.length > 0) {
  if (violations.length > 0) {
    console.error("view-model boundary violated (FR-043):\n");
    for (const violation of violations) {
      console.error(`  ${violation.file}\n    imports ${violation.specifier} — ${violation.why}`);
    }
    console.error(
      "\nThe view model is the only tested surface of this application. Move the\n" +
        "decision back into src/console/model/, or it ships with no coverage at all.",
    );
  }

  if (networkViolations.length > 0) {
    console.error("\ncomponents reached the network directly (SC-013):\n");
    for (const violation of networkViolations) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
    }
    console.error(
      "\nRequests are constructed in src/console/model/client.ts and executed in\n" +
        "src/transport.ts. A second call site puts FR-041's guarantee — that the\n" +
        "console reaches exactly the endpoints ALLOWED names — out of reach of any test.",
    );
  }

  process.exit(1);
}

console.log("view-model boundary: clean");
console.log("components reach the network only through src/transport.ts: clean");
