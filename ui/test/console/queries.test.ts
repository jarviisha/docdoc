import assert from "node:assert/strict";
import { test } from "node:test";

import type { Intent } from "../../src/console/model/client.ts";
import { invalidatedBy, keyOf } from "../../src/console/model/queries.ts";

test("a read's key is its type, then the whole intent", () => {
  assert.deepEqual(keyOf({ type: "run-detail", runId: "r1" }), ["run-detail", { type: "run-detail", runId: "r1" }]);
});

const STALE: [Intent, readonly (readonly [Intent["type"]])[] | "all"][] = [
  [{ type: "cancel-run", runId: "r" }, [["run-detail"], ["run-delivery"], ["list-runs"], ["overview"]]],
  [{ type: "submit-ocr", tenantId: "t" }, [["list-runs"], ["overview"]]],
  [{ type: "submit-extraction", tenantId: "t", schema: "s" }, [["list-runs"], ["overview"]]],
  [{ type: "issue-credential" }, [["list-credentials"]]],
  [{ type: "revoke-credential", credentialId: "c" }, [["list-credentials"]]],
  [{ type: "register-callback", tenantId: "t" }, [["list-callbacks"]]],
  [{ type: "revoke-callback", tenantId: "t", callbackId: "c" }, [["list-callbacks"]]],
  [{ type: "record-check", runId: "r" }, [["run-checks"]]],
  [{ type: "start-evaluation", tenantId: "t", schema: "s" }, [["list-evaluations"]]],
  [{ type: "erase-tenant", tenantId: "t" }, "all"],
  [{ type: "erase-document", tenantId: "t", blobId: "b" }, "all"],
  [{ type: "list-runs" }, []],
  [{ type: "run-detail", runId: "r" }, []],
  [{ type: "overview" }, []],
];

for (const [intent, stale] of STALE) {
  test(`${intent.type} makes stale: ${JSON.stringify(stale)}`, () => {
    assert.deepEqual(invalidatedBy(intent), stale);
  });
}
