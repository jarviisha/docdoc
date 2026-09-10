# Erasing one customer's data

A customer asks to be removed. This is what to run, what it does, and the one
case where the obvious command is the wrong one.

## Before you start

Erasure needs the run-state database and the store the deployment writes to:

```bash
export DOCDOC_RUN_DATABASE_URL='postgresql://docdoc:docdoc@localhost:5432/docdoc'
export DOCDOC_STORE_ROOT=/var/lib/docdoc      # or DOCDOC_STORE_URL for S3
```

## Erase a named tenant

```bash
docdoc erase --tenant acme
```

```json
{"scope": "tenant", "target": "acme",
 "runs": 128, "artifacts": 512, "blobs": 128, "rows": 4, "degraded": false}
```

What went: that tenant's blobs, its artifacts, its run rows, and every row the
operational tables held for it — corrections, callbacks, deliveries, and limit
counters.

What stayed: **tombstones**, one per removed run. Four fields each — identity,
tenant, deletion time, policy — so the tenant can be told a run was here and is
gone without anything being retained about what it held.

**In-flight runs are cancelled first**, so no worker is left writing artifacts
into a namespace that has just disappeared.

**It is idempotent.** Run it twice; the second reports zeros and succeeds. An
operator acting under time pressure runs this twice, and neither run should fail.

## Erase one document

```bash
docdoc erase --document sha256:1c9f…
```

The document, the runs over it, and the artifacts no *surviving* run of that
tenant still names. The tenant's callbacks and counters stay — the tenant is not
being erased.

## Over HTTP

```bash
curl -X DELETE http://localhost:8000/v1/admin/tenants/acme \
  -H "authorization: Bearer $ADMIN_KEY"
```

`200`, synchronous, with the same body. Requires the `admin` scope; a tenant
credential gets `404`, not `403`.

It removes at most **10 000 runs per call**. A larger tenant goes through the
command line, which is bounded only by your patience.

## The default tenant is different, and the difference matters

With authentication off there is one implicit tenant called `default`, and **its
namespace is the store root itself**. So "erase the default tenant" and "empty
the entire store" are the same directory.

The route refuses it outright:

```bash
curl -X DELETE http://localhost:8000/v1/admin/tenants/default \
  -H "authorization: Bearer $ADMIN_KEY"
```

```json
{"error": "default_tenant_erasure_refused",
 "detail": "the default tenant's namespace is the store root; use the CLI"}
```

The command line does the *narrow* thing — that tenant's run-derived content, and
nothing else:

```bash
docdoc erase --tenant default
```

Emptying the root outright is a separate flag, and it exists at no URL:

```bash
docdoc erase --tenant default --purge-store-root      # removes EVERYTHING
```

That includes every artifact `docdoc extract` ever wrote from the command line,
everything a library caller produced, and every other tenant's content if the
store is shared. It is a flag somebody has to type on purpose.

## Verifying

The tenant's runs answer `410` to that tenant and `404` to everybody else:

```bash
curl -i http://localhost:8000/v1/runs/0f8b… -H "authorization: Bearer $ACME_KEY"
```

```json
{"error": "run_erased", "run_id": "0f8b…",
 "deleted_at": "2026-09-09T00:00:00Z", "policy": "erasure:tenant"}
```

And the tables hold nothing:

```sql
SELECT count(*) FROM runs         WHERE tenant_id = 'acme';   -- 0
SELECT count(*) FROM corrections  WHERE tenant_id = 'acme';   -- 0
SELECT count(*) FROM callbacks    WHERE tenant_id = 'acme';   -- 0
SELECT count(*) FROM deliveries   WHERE tenant_id = 'acme';   -- 0
SELECT count(*) FROM run_tombstones WHERE tenant_id = 'acme'; -- one per removed run
```

## If the store is unreachable

Nothing is removed — not the content, and **not the run rows either**. The report
comes back `"degraded": true`.

That is deliberate: the run row holds the list of artifacts to delete, so
removing it while the content survives would lose the only record of what still
needs removing. Fix the store and run the command again.

## See also

- [retention](../docs/concepts/retention.md) — the sweep, and the set difference
- [ADR-0015](../docs/adr/0015-deletion-over-a-content-addressed-store.md)
