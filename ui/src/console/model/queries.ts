/**
 * How the console's reads are cached: one key per intent, and which keys a
 * successful write makes stale.
 *
 * The key leads with the intent's type so a write can mark a whole family
 * stale by prefix (every `list-runs`, whatever its filters) without knowing
 * which filters are on screen. Nothing here is kept at rest: the cache lives
 * in memory only and is cleared on sign-out (specs/011 FR-007).
 */

import type { Intent } from "./client.ts";

/** A read's cache key: its type, then the whole intent. */
export function keyOf(intent: Intent): readonly [Intent["type"], Intent] {
  return [intent.type, intent];
}

/**
 * The key prefixes a successful `intent` makes stale. Erasure removes data any
 * screen might show, so it makes everything stale; a read makes nothing stale.
 */
export function invalidatedBy(intent: Intent): readonly (readonly [Intent["type"]])[] | "all" {
  switch (intent.type) {
    case "cancel-run":
      return [["run-detail"], ["run-delivery"], ["list-runs"], ["overview"]];
    case "submit-ocr":
    case "submit-extraction":
      return [["list-runs"], ["overview"]];
    case "issue-credential":
    case "revoke-credential":
      return [["list-credentials"]];
    case "register-callback":
    case "revoke-callback":
      return [["list-callbacks"]];
    case "record-check":
      return [["run-checks"]];
    case "start-evaluation":
      return [["list-evaluations"]];
    case "erase-tenant":
    case "erase-document":
      return "all";
    default:
      return [];
  }
}
