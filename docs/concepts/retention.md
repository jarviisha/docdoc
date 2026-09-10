# Retention and erasure

Runs accumulate. A deployment that ran for a year has a year of run rows, a year
of artifacts, and a year of documents, and until Milestone 10 there was no
in-product way to remove any of it. This is that way, and it is two separate
things that share one mechanism:

- **Retention** removes runs that have aged out, automatically, on a schedule.
- **Erasure** removes one customer's data, or one document's, because somebody
  asked.

Both are **off until configured**. A deployment that sets nothing sweeps nothing
and behaves exactly as it did before.

## The set difference, and the bug it avoids

The sweep computes a **difference**, never a complement:

```text
candidates = ⋃ stage_outcomes[].artifact_id over the runs being removed
survivors  = ⋃ stage_outcomes[].artifact_id over that tenant's remaining runs
delete       candidates − survivors
```

An artifact becomes a candidate **only by having been named by a run that is
being removed**.

The rejected form is one word away in English. "Delete every artifact no
surviving run references" is a *complement*, and it deletes every artifact
`docdoc extract` ever wrote from the command line, everything a library caller
produced, and every artifact the recorder wrote — because none of those has a run
row. They are unreferenced by construction.

That is the most dangerous bug available in this feature and it is designed out
rather than tested for. `tests/unit/test_sweep_is_a_difference.py` exists to fail
if anybody converts one into the other.

**Survivorship is read, not derived.** A run row's `stage_outcomes` already
records an `artifact_id` per stage, so the sweep needs no parser, no schema
registry, and no options hash. Re-deriving the chain would need the parser
version that was in effect when the run executed, which a reconfigured deployment
no longer has — it would silently compute a different set and miss artifacts,
which is the worst available failure in a routine whose job is removal.

## Order, and why a crash is safe

Artifacts and blobs, then the tombstone, then the run row.

A crash between any two steps leaves the run present with its content partly
gone, and the next sweep recomputes the difference from what remains and
finishes. The reverse order loses the list of what to delete: the run row is what
names the artifacts.

**An unreachable store deletes nothing at all** — not the content, and not the
run row either. Removing the row while the content survives would lose the only
record of the work.

## The tombstone

A removed run leaves four fields: identity, tenant, deletion time, and policy
name. No status, no blob id, no schema identity, no stage outcomes. A tombstone
that carried what the run held would be a way of retaining what was deleted.

`GET /v1/runs/{run_id}` then answers:

| Status | Condition |
|---|---|
| `200` | the run, owned by this tenant |
| `410` | a tombstone exists for this tenant — it was here and is gone |
| `404` | no run and no tombstone, **or** either belonging to another tenant |

The `410` and the `404` differ **in kind**, which is the point. A tenant's own
erased run is knowable to that tenant; to anyone else it is byte-identical to an
identifier that never existed.

`RunStatus` gains no member for any of this. It is closed at `queued`, `running`,
`succeeded`, `failed`, `cancelled`, and a client switching exhaustively on it
keeps being right. A removed run is gone *as a run*; what remains is a tombstone,
in its own table.

## The default tenant is the store root

With authentication off there is one implicit tenant, and its namespace is the
store root itself — not `t/default/`. So "erase the default tenant" and "empty
the entire store" are the same operation, including everything written before
authentication was enabled and everything the command line ever wrote.

Therefore:

- `DELETE /v1/admin/tenants/default` is **refused**, always, with `409`.
- `docdoc erase --tenant default` falls back to the set-difference path: that
  tenant's run-derived content, and nothing else.
- Emptying the root outright needs `--purge-store-root`, which exists on the
  command line and at **no URL**.

This is not caution invented after the fact. ADR-0014's Consequences section
predicted it by name: *"a naive 'delete tenant' implementation in Milestone 10
would delete everything."*

## What erasure removes

Erasing a tenant removes its blobs, its artifacts, its run rows, and every row
the operational tables hold for it — `corrections`, `callbacks`, `deliveries`,
and `limit_counters`.

`corrections` is why that list matters. It holds the predicted and corrected
**values** a reviewer stated, so it is the one table here that can carry
document-derived content; an erasure that left it behind would answer "erased"
about data that is still there.

Tombstones are **not** removed. They carry an identity, a tenant, a time, and a
policy, and they are what lets the owner be told their run was here and is not.

**In-flight runs are cancelled first.** Erasure must never leave a worker writing
artifacts into a namespace that has just disappeared.

**It is idempotent.** A second call removes nothing and succeeds, and erasing a
tenant that never existed succeeds having removed nothing — because an operator
running this twice under time pressure is the normal case, not the exceptional
one.

## Corrections pin their runs

A run whose result carries a live correction is not swept, and a correction has
its own retention deadline configured independently of a run's. Where the two
disagree, the longer wins. See [corrections and routing](routing.md).

## When it runs

The worker performs one bounded sweep batch between claims, so a deployment that
runs a worker gets retention without arranging anything. There is no reaper
process and no cron entry — Milestone 9's argument against the first is the
argument against the second, which is a process to deploy, monitor, and have fail
silently.

**A deployment running no worker sweeps nothing.** That is stated rather than
implied: retention is work, and work happens where work happens.

`docdoc sweep [--once]` is the same operation on demand.

## Limits

- The sweep is **bounded per batch**, so a year of accumulated rows makes
  progress across many ticks rather than attempting one unbounded delete.
- `DELETE /v1/admin/tenants/{tenant_id}` removes at most 10 000 runs per call. A
  larger tenant goes through `docdoc erase`, which is bounded only by patience.
- Retention acts on **terminal** runs only. A queued or running run is never
  swept regardless of its deadline, and neither is one holding an unexpired
  lease: a retention policy that can delete work in flight is a policy that loses
  paid work.
- A deleted artifact that is later recomputed derives the **same** identity from
  the same inputs. Content-addressing promises that, and deletion does not break
  it.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `DOCDOC_RUN_RETENTION_DAYS` | how long a run is kept | **unset — nothing is swept** |
| `DOCDOC_RUN_SWEEP_BATCH` | runs removed per sweep invocation | 500 |
| `DOCDOC_MAINTENANCE_INTERVAL_SECONDS` | how often a worker ticks | 60 |
| `DOCDOC_MAINTENANCE_BUDGET_MS` | how long one tick may take | 5000 |
| `DOCDOC_CORRECTION_RETENTION_DAYS` | how long a correction is kept | unset — as long as its run |

`docdoc sweep` takes `--retention-days` and `--batch` for the same two settings.
The worker has no flags for them: a process that sweeps between claims is
configured by the deployment rather than by an invocation.

## See also

- [ADR-0015 — deletion over a content-addressed store](../adr/0015-deletion-over-a-content-addressed-store.md)
- [ADR-0014 — tenant scoping and store namespacing](../adr/0014-tenant-scoping-and-store-namespacing.md)
- [runs](runs.md) — what a run is, and what the worker does
