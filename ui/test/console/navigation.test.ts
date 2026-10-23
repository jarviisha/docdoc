/**
 * The tenant jumps: a tenant row's own shortcuts into its runs, credentials,
 * callbacks, and erasure — and the one case the frame leaves alone, the area
 * identifiers `Shell.tsx` used to declare on its own.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { jumpTo, TENANT_JUMPS } from "../../src/console/model/navigation.ts";

test("tenant jump presets the area", () => {
  assert.deepEqual(jumpTo("erasure", "acme"), { area: "erasure", tenant: "acme" });
});

test("the tenant jumps are runs, credentials, callbacks, and erasure, in that order", () => {
  assert.deepEqual(TENANT_JUMPS, [
    { area: "runs", label: "Runs" },
    { area: "credentials", label: "Credentials" },
    { area: "callbacks", label: "Callbacks" },
    { area: "erasure", label: "Erase…" },
  ]);
});

test("quality is an area", () => {
  assert.deepEqual(jumpTo("quality", "acme"), { area: "quality", tenant: "acme" });
});
