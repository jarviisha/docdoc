#!/usr/bin/env node
/**
 * T010 — every control is named and reachable by keyboard (FR-045a, SC-014a).
 *
 * A source check rather than a test, for the reason `check-readonly.mjs` records
 * about itself: the rendering layer carries no automated test by decision
 * (`specs/008`), and this milestone adds no browser driver. The only inspection
 * available is reading the source, so it is the one that runs.
 *
 * **T076 added the third rule**: a component that shows a transient status
 * message must have somewhere for a screen reader to hear it. FR-045b requires
 * it and, until convergence looked, the console had **zero** live regions while
 * the viewer had one — a requirement about what a screen reader hears, checked
 * by nobody.
 *
 * **What it cannot see**, stated here because a guard whose ceiling is
 * undocumented is one a reader trusts past its limit: focus order, colour
 * contrast, whether a live region wraps the *right* message or announces at the
 * right moment, whether a dialog traps focus. Rule three proves a region exists
 * in the file, not that it is around the message that matters. SC-014a claims
 * *reachable and named*, which is what rules one and two measure. FR-045c
 * claims *no conformance level*, which is what the documentation says. The gap
 * between those sentences is deliberate.
 *
 * Takes an optional directory argument, as T014 requires.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const DEFAULT_DIR = join(ROOT, "src", "console", "components");

/** Elements the platform already makes focusable and operable. */
const INTERACTIVE = /^<(button|a|input|select|textarea|summary|label)\b/i;

/** A component, not a tag: `<Button>`, `<TextInput>`, `<SelectableCard>`. */
const COMPONENT = /^<[A-Z]/;

const COMMENT = /^\s*(\*|\/\/|\/\*)/;

/**
 * What a transient status message looks like in this tree.
 *
 * A `Banner` is how every one of them is rendered, and "Loading" is the one that
 * is plain text. Both appear and disappear in response to something the operator
 * did or something the deployment answered — which is exactly FR-045b's subject.
 */
const TRANSIENT = [/<Banner\b/, /Loading…|Loading\.\.\./];

/** Somewhere a screen reader is told. */
const ANNOUNCES = /aria-live\s*=|role\s*=\s*["'{]?\s*["']?status["']?/;

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
    else if (/\.(tsx|jsx)$/.test(entry.name)) files.push(path);
  }
  return files;
}

/**
 * The opening tag a handler is attached to.
 *
 * JSX spans lines, so the tag is found by walking backwards to the nearest `<`
 * that starts an element. Crude, and it has to be: a parser would be precise and
 * would cost the dependency research R4 spent an argument avoiding.
 */
function openingTag(lines, index) {
  for (let cursor = index; cursor >= 0 && cursor > index - 12; cursor -= 1) {
    const match = /<[A-Za-z][A-Za-z0-9.]*/.exec(lines[cursor] ?? "");
    if (match) return { tag: lines[cursor].slice(match.index), at: cursor };
  }
  return null;
}

/** The whole element, so props spread over several lines are still seen. */
function elementText(lines, from) {
  return lines.slice(from, from + 12).join(" ");
}

async function scan(dir) {
  const unreachable = [];
  const unnamed = [];
  const unannounced = [];

  for (const file of await walk(dir)) {
    const source = await readFile(file, "utf8");
    const lines = source.split("\n");

    // 3. A component that shows a transient message with nowhere to hear it.
    //
    //    File-level, not element-level: matching a region to the message it
    //    wraps needs the tree this script deliberately does not build. What it
    //    catches is the failure that actually happened — a whole console with no
    //    live region in it at all.
    const code = lines.filter((line) => !COMMENT.test(line)).join("\n");
    if (TRANSIENT.some((rule) => rule.test(code)) && !ANNOUNCES.test(code)) {
      unannounced.push({ file: relative(ROOT, file) });
    }

    lines.forEach((line, index) => {
      if (COMMENT.test(line)) return;

      // 1. A click handler on something the platform does not make interactive.
      if (/\bonClick\s*=/.test(line)) {
        const opening = openingTag(lines, index);
        if (opening && !INTERACTIVE.test(opening.tag) && !COMPONENT.test(opening.tag)) {
          const element = elementText(lines, opening.at);
          const hasRole = /\brole\s*=/.test(element);
          const hasTabStop = /\btabIndex\s*=/.test(element);
          const hasKeys = /\bonKeyDown\s*=|\bonKeyUp\s*=/.test(element);
          if (!hasRole || !hasTabStop || !hasKeys) {
            unreachable.push({
              file: relative(ROOT, file),
              line: opening.at + 1,
              what: opening.tag.split(/\s/)[0],
            });
          }
        }
      }

      // 2. A native control with no accessible name. `<Field>`-wrapped controls
      //    get theirs from the label the wrapper renders, so an `id` that a
      //    label can point at counts.
      if (/^\s*<(input|select|textarea)\b/i.test(line)) {
        const element = elementText(lines, index);
        const named =
          /\baria-label\s*=/.test(element) ||
          /\baria-labelledby\s*=/.test(element) ||
          /\bid\s*=/.test(element);
        if (!named) {
          unnamed.push({ file: relative(ROOT, file), line: index + 1, what: line.trim().slice(0, 40) });
        }
      }
    });
  }

  return { unreachable, unnamed, unannounced };
}

// `resolve` and not `join`: a test passes an absolute fixture path, and joining
// that onto the cwd produces a path that exists nowhere.
const target = process.argv[2] ? resolve(process.cwd(), process.argv[2]) : DEFAULT_DIR;
const { unreachable, unnamed, unannounced } = await scan(target);

if (unreachable.length > 0 || unnamed.length > 0 || unannounced.length > 0) {
  if (unreachable.length > 0) {
    console.error("a control is reachable only with a pointer (FR-045a):\n");
    for (const violation of unreachable) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what} has onClick`);
    }
    console.error(
      "\nUse a <button>, or give the element a role, a tabIndex, and a key handler.\n" +
        "The erasure confirmation is the reason this rule is not advisory: a dialog\n" +
        "that erases a tenant and can only be reached with a mouse is a trap.",
    );
  }

  if (unnamed.length > 0) {
    console.error("\na control has no accessible name (FR-045a):\n");
    for (const violation of unnamed) {
      console.error(`  ${violation.file}:${violation.line} — ${violation.what}`);
    }
    console.error("\nAdd aria-label, aria-labelledby, or an id a <Field> label points at.");
  }

  if (unannounced.length > 0) {
    console.error("\na status message has nowhere to be heard (FR-045b):\n");
    for (const violation of unannounced) {
      console.error(`  ${violation.file}`);
    }
    console.error(
      "\nThis file shows a Banner or a loading message and contains no live region.\n" +
        'Wrap it: <div role="status" aria-live="polite">…</div>, or "assertive" when\n' +
        "the operator has just destroyed something and the report is the only record.\n" +
        "The viewer's Running.tsx is the pattern.",
    );
  }

  process.exit(1);
}

console.log("every control is named and keyboard-reachable: clean");
console.log("every status message has a live region: clean");
