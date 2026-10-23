import assert from "node:assert/strict";
import { test } from "node:test";

import { POLL_MS } from "../../src/console/model/polling.ts";

test("the poll interval is five seconds", () => {
  assert.equal(POLL_MS, 5000);
});
