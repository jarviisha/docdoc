# ADR-0019: An Operations Console Is Permitted; A Review Platform Stays Deferred

- **Status**: Accepted
- **Date**: 2026-09-11
- **Implements**: Milestone 11 (`specs/011-operations-console/spec.md`, to be written against this decision)
- **Relates to**: [ADR-0014](0014-tenant-scoping-and-store-namespacing.md) (tenant scoping, and the existence oracle), [ADR-0015](0015-deletion-over-a-content-addressed-store.md) (erasure), [ADR-0016](0016-credential-lifecycle.md) (credential lifecycle, and the administrative scope), [ADR-0017](0017-confidence-routing-policy.md) (routing has two outcomes and no disposition)
- **Principles engaged**: IX (Evaluation and human correction are product features), XI (MVP discipline), II (nothing here reads an untrusted signal)

## Context

The constitution's *Deferred technology* list names **"a full review UI"**, and Principle XI says
such a thing MUST NOT appear without an approved amendment. That sentence has been load-bearing
four times: Milestone 6 declined to build one, Milestone 8 shipped a viewer and had to argue in
`specs/008/plan.md` gate 12 that a read-only surface is not the deferred thing, ADR-0017 used it to
refuse a `reject` outcome, and `specs/010`'s Out of Scope deferred "an operator or administrative
interface" to Milestone 11 by name.

So the question is not whether an administrative interface is wanted. Milestone 10 built credential
issuance and revocation, tenant and document erasure, delivery records, and routing outcomes, and
`specs/010/spec.md` said plainly that it "makes such an interface *possible* … it does not build
one." The question is where the fence goes, because Milestone 8's fence — *it renders and never
edits* — cannot be reused. A console that revokes a credential edits by definition.

Answering by reading the existing sentence would invert Governance's precedence rule, exactly as
v1.6.0 and v1.8.0 each recorded about themselves: a spec that reinterprets the constitution in order
to comply with it has made the constitution the junior document. Hence an amendment, and hence this
ADR to say what the amendment's new line means concretely.

## Decision

### 1. The permitted thing is an *operations console*, and it is defined by who it serves

An operations console serves the **person who runs the deployment**: it answers what ran, what the
deployment currently holds, which credentials exist, and what was delivered. Its unit of work is the
*deployment*.

A review platform serves the **person who checks documents**: it assigns items to reviewers, holds a
queue of work per person, tracks what stage of review each item is in, and records a disposition.
Its unit of work is the *reviewer*.

The distinction is who the surface is *about*, and it survives both directions of pressure: a console
that grew a per-reviewer inbox has become the deferred thing however it is named, and a review
platform does not become permitted by being small.

### 2. Five concrete nouns are forbidden, and they are the whole fence

The console MUST NOT hold, display, or let anyone set:

- **reviewer assignment** — an item belonging to a person;
- **a review queue** — a per-person ordered list of work;
- **workload** — a count of work per person, or any balancing of it;
- **a review state** — an item's position in a review lifecycle, beyond the five-member closed run
  status set `specs/009` fixed and FR-004a preserved;
- **a disposition** — accept, reject, approve, or any outcome beyond ADR-0017's `automatic` and
  `review`, which are decisions about a *result* and not about a *review*.

Stated as nouns rather than as a principle, because a principle is argued at review time and a noun
is grepped. `ui/scripts/check-console-boundary.mjs` fails the build on these identifiers appearing
under the console's source, in the tradition of `check-readonly.mjs`: the argument that Milestone 8
would not become a review UI is only still true because a script has been re-running it ever since.

### 3. The console introduces no server-side concept

Every write it performs goes through a route Milestone 10 already ships and already tested:
`POST`/`DELETE /v1/admin/credentials`, `DELETE /v1/admin/tenants/{id}`,
`DELETE /v1/admin/documents/{blob_id}`. No new mutation, no new state, no table.

It needs exactly one new **read**: a tenant-scoped list of runs, because `GET /v1/runs/{run_id}`
answers only for an identifier the caller already has, and a console that cannot enumerate is a
console that cannot open. The list is a read of state that already exists, scoped by tenant **as a
predicate in the query** like every other route since ADR-0014 §3, so an unknown run and another
tenant's run stay indistinguishable and the existence oracle stays closed.

A second read may be added for administrative scope — a list across tenants — and if it is, it is
gated on `ADMIN_SCOPE` and answers `404` to a non-admin exactly as ADR-0016 §6 requires. It does not
answer `403`, which would confirm the route exists.

**Every route the console needs is named around the fifteen words
`tests/contract/test_no_review_platform.py` forbids in a path** — `assign`, `reviewer`, `queue`,
`worklist`, `inbox`, `task`, `approve`, `reject`, `escalate`, and their variants. That test is left
unchanged, and this is a real constraint rather than a formality: an operations console genuinely
wants to show how much work is waiting for the workers, and the obvious name for it contains
`queue`. It is `GET /v1/admin/backlog`, or another name outside the list.

Renaming is the cheaper side of the trade. The alternative is widening the test to "one of these
words **plus** a noun about a person", which is a guard that has started reasoning — and a guard that
reasons is one a future route can argue its way past. A word list either matches or does not, which
is the only property that makes it still true in two years. The console pays a slightly worse route
name for it; the milestone that added the test paid nothing for it at all, which is what made the
cost invisible until this ADR.

### 4. The credential is held in memory for the session and is never persisted

The operator pastes an API key; it lives in the page's state and is gone on reload. No cookie, no
token endpoint, no login route, no server-side session.

This is the smaller decision of the two available and the more defensible one. A cookie session means
a login route, a session table, CSRF, and an expiry policy — four new security surfaces written by
this project, to replace a credential the deployment already has and already rotates through
ADR-0016. Deferring that is not laziness about security; issuing a second credential type to guard
the first is what would need the justification.

Milestone 8's no-persistence rule (`specs/008` FR-032, enforced across the whole of `ui/src`)
therefore extends to the key with no new mechanism: `localStorage`, `sessionStorage`, IndexedDB and
the Cache API are already build failures in this tree. The accepted cost is that a reload asks for
the key again, and it is the same cost the viewer already pays for the document.

A deployment that wants single sign-on puts an authenticating proxy in front of the console. That is
the operator's infrastructure, and docdoc ships no part of it.

### 5. The viewer's read-only guarantee is unchanged, not relaxed

`check-readonly.mjs` currently scans `ui/src/components/`. The console's sources move under a
sibling directory and the guard is re-pointed at the viewer's own, so Milestone 8's claim keeps being
machine-checked rather than quietly inherited by a tree that now contains forms. The claim narrows
from "this repository's UI edits nothing" to "**the viewer** edits nothing", and that is the claim
Milestone 8 actually made.

### 6. Recording a correction is not part of this decision

The route exists (`POST /v1/runs/{run_id}/corrections`, FR-078) and a console could call it. Whether
a *surface* for it is the console's or a later milestone's is deliberately left open here, because it
is the one feature that sits on the line this ADR draws: one reviewer annotating one value is
Principle IX's product feature, and the same screen with an inbox beside it is the platform Principle
IX forbids. Milestone 11 does not build it. A milestone that does cites this ADR and says which side
of §1 it lands on.

## Consequences

**Accepted:**

- The operator pastes a key on every reload. Not fixed with storage; storage is what §4 forbids.
- Without §3's optional cross-tenant read, the console shows one tenant at a time — the tenant the
  pasted key resolves to.
- Limits and retention are configuration, not routes: the console can show that a run was refused,
  and cannot show how close a tenant is to a threshold. A `GET /v1/admin/limits` would be a new
  server-side read and is not justified by "the page would look better with a gauge".
- Erasure targets are typed, not picked from a list, until a tenant listing exists.
- The five forbidden nouns are unavailable even where one would genuinely help. That is the point of
  fixing them as nouns; the escape hatch is an ADR that supersedes this one, not a judgement call in
  a PR.

**Rejected alternatives:**

- **A cookie session with a login route.** Four new security surfaces to guard a credential that
  already exists (§4).
- **Keeping the console outside the repository.** It would be free of every constraint here, which
  is the argument against it: the fence is the deliverable.
- **Reusing `check-readonly.mjs` by exempting the console.** An exemption list is a guard that
  eventually says nothing. Two guards over two directories each keep saying something true.
- **Declaring the console "not a UI" and skipping the amendment.** The reinterpretation Governance's
  precedence rule exists to prevent.
