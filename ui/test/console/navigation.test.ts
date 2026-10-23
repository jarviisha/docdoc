/**
 * The tenant jumps: a tenant row's own shortcuts into its runs, credentials,
 * callbacks, and erasure — and the one case the frame leaves alone, the area
 * identifiers `Shell.tsx` used to declare on its own.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { AREA_PATHS, areaOf, jumpTo, TENANT_JUMPS } from "../../src/console/model/navigation.ts";

test("tenant jump goes to the area's path with the tenant", () => {
  assert.deepEqual(jumpTo("erasure", "acme"), { to: "/erasure", search: { tenant: "acme" } });
});

test("every area has a path and areaOf inverts it", () => {
  for (const [area, path] of Object.entries(AREA_PATHS)) assert.equal(areaOf(path), area);
});

test("areaOf reads nested, prefixed and unknown paths", () => {
  assert.equal(areaOf("/runs/abc"), "runs");
  assert.equal(areaOf("/console/runs/abc"), "runs");
  assert.equal(areaOf("/console/"), "overview");
  assert.equal(areaOf("/console"), "overview");
  assert.equal(areaOf("/nope"), null);
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
  assert.deepEqual(jumpTo("quality", "acme"), { to: "/quality", search: { tenant: "acme" } });
});

test("schemas is an area at /schemas", () => {
  assert.equal(AREA_PATHS.schemas, "/schemas");
  assert.equal(areaOf("/console/schemas"), "schemas");
});
