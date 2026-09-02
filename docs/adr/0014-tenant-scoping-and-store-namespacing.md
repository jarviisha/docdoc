# ADR-0014: Tenant Scoping, Store Namespacing, and the Existence Oracle

- **Status**: Accepted
- **Date**: 2026-08-28
- **Implements**: Milestone 9 (`specs/009-asynchronous-runs/spec.md`), FR-084, FR-084a, FR-086, FR-088, FR-089
- **Relates to**: [ADR-0002](0002-blob-and-document-identity.md) (identity), [ADR-0003](0003-content-addressed-artifact-chain.md) (artifact chain), [ADR-0010](0010-artifact-store-and-job-model.md) (store layout), [ADR-0013](0013-asynchronous-run-model.md) (the milestone this serves)
- **Principles engaged**: VIII (Reproducibility, provenance, and versioning), XI (MVP discipline), and MVP Scope Constraints §Security

## Context

Milestone 9 makes docdoc a deployment two customers can share. Nothing before it had to answer what
one tenant may learn about another, because there was only ever one.

The question is sharper here than in most systems, and the reason is a property the project spent
eight milestones building on purpose. **Identity is derived from content.** `blob_id` is the hash of
the bytes (ADR-0002); `artifact_id` is derived from a stage's inputs and `processing_id` is the
terminal one (ADR-0003). Two tenants who submit the same invoice against the same schema therefore
arrive at *the same identities*, independently and unavoidably. That is correct — it is what makes
reuse work at all — and it means a shared store is a shared namespace whether anyone intended one or
not.

The consequence is an **existence oracle**. If the store is shared, a tenant can learn that another
tenant holds a particular document by submitting it and observing that the result comes back
immediately and costs nothing. Milestone 9's FR-066 requires a cross-tenant read to be
indistinguishable from a read of something that never existed, and SC-008 asserts the responses are
byte-identical. Neither reaches this: latency is not a response body, and a provider invoice is not a
status code. A design that scopes only at the read boundary satisfies the letter of both and leaks
anyway.

A second question arrives with the first. Every existing deployment has a store full of content
written with no tenant at all. Whatever namespacing is chosen has to say what happens to it, and
Milestone 9's SC-018 requires an upgrade to change nothing for a deployment that never enables
authentication.

## Decision

### 1. The store is namespaced per tenant, above the fan-out

```text
<root>/t/<tenant_id>/blobs/<aa>/<full-hash>
<root>/t/<tenant_id>/artifacts/<aa>/<full-hash>.json
```

The tenant segment sits **above** ADR-0010 §1's two-character fan-out, which keeps that decision's
reason intact — a flat directory of a hundred thousand entries is slow and free to avoid — and makes
per-tenant deletion, which Milestone 10 needs, a prefix operation rather than a scan.

`tenant_id` is validated as `[a-z0-9_-]{1,64}` at the authentication boundary, so a value that could
escape a path segment never reaches a store. The stores do not re-validate: one validation point that
always runs beats two that can disagree.

### 2. Namespacing changes location and never identity

`blob_id`, `artifact_id`, `content_id`, and `processing_id` are computed exactly as before. Two
tenants processing identical bytes still derive identical identities — they simply never see each
other's copies.

Mixing the tenant into the identity derivation was the obvious alternative and is rejected outright.
It would make `processing_id` a function of *who asked* rather than of the inputs, which contradicts
ADR-0003's entire basis and would break the one property Milestone 9 depends on for correctness: that
a redelivered run recomputes the same terminal identity (ADR-0013 §4).

### 3. The default tenant's namespace is the store root itself

```text
<root>/blobs/<aa>/<full-hash>                  default tenant — the Milestone 8 layout, unmoved
<root>/t/<tenant_id>/blobs/<aa>/<full-hash>    every other tenant
```

An unconditional prefix would put every existing deployment's content at a path the new code never
looks at. The result is the worst kind of regression: **correct answers, silently re-paying for every
parse**, because a miss is indistinguishable from an absence. Milestone 9's SC-018 exists to catch
exactly that, and the first draft of its own specification would have failed it — the requirement said
namespacing was unconditional while the criterion required unprefixed content to stay readable, and
nobody noticed until `/speckit-clarify` put the two side by side.

Two alternatives bought path uniformity and were rejected on cost. **Relocating on upgrade** means
copying every artifact a deployment has ever written; on an object store a move is a copy followed by
a delete, and this would run for every operator including those who never enable authentication.
**A read-through fallback to the legacy path** avoids moving anything and pays a second round trip on
every *miss* — which is the common case for a new document, not the rare one — and leaves a
compatibility path in the read hot loop permanently.

The price of this decision is one branch in path derivation, and it is a real one: it is the only
conditional in a derivation chain the project otherwise keeps free of them. It is accepted because it
is a **stated compatibility rule** rather than a behavioural difference between tenants, and it is
required to carry a comment saying so, because a future reader tidying it away would strand every
existing deployment's data.

### 4. Cross-tenant reuse is forfeited, deliberately

Two tenants submitting identical bytes pay for two parses and two extractions. Reuse **within** a
tenant — the case ADR-0003 was written for, where changing a prompt reuses the parse — is untouched.

This is the price of §1, and it is paid knowingly. The bet is that identical documents arriving from
two different customers is rare outside narrow industries, while the oracle would be present in every
deployment on every day. A deployment where cross-tenant duplication is genuinely common — a shared
clearing house, say — should raise that as a new decision rather than quietly widen the namespace.

### 5. The oracle is closed on cost and timing, not only on response bodies

FR-066 and SC-008 require cross-tenant responses to be byte-identical to non-existence. This ADR adds
the half those cannot express: a tenant submitting a document another tenant has already processed
must invoke the parser and the model adapter **exactly as many times as a first-ever submission**.
Milestone 9's SC-017 measures it on invocation counters.

Recorded as a decision because it is the requirement that determines the design. Scoping at the read
boundary over a shared store satisfies every criterion that examines a response, and leaks through the
two channels a response does not carry.

### 6. Authentication is off by default, and a deployment without it has one implicit tenant

An existing deployment upgrades with no configuration change and behaves exactly as it did under
Milestone 8: no credential on any route, one implicit tenant owning all content, stored where it
already is by §3.

**This is the compatible default rather than the safe one**, and the distinction is not softened
anywhere. A deployment that never enables authentication is exactly as exposed as Milestone 8 was, and
the README is required to keep saying so. What the default buys is that upgrading breaks nothing; what
it costs is that security is opt-in.

Enabling authentication over existing content assigns that content to a tenant **named in
configuration**, by an explicit, idempotent step. The system infers no owner and leaves nothing
unreachable. Under §3 that assignment needs no data movement when the configured tenant is the default
one, which is the ordinary case.

## Consequences

**A shared deployment is safe for more than one customer, and separate deployments remain the answer
for anyone who needs physical separation.** Isolation here is scoping, not infrastructure: tenants
share a database, a store, and a worker pool.

**Milestone 10's per-tenant deletion is a prefix operation.** That is a direct consequence of putting
the tenant above the fan-out in §1 rather than inside the hash, and it is why the layout is worth
deciding now even though deletion is deferred.

**The default tenant is the one whose deletion is dangerous.** Its prefix is the store root, so a
naive "delete tenant" implementation in Milestone 10 would delete everything. That is the correct
semantics — the default tenant *does* own everything at the root — and it is written down here because
it is the kind of correctness that reads as a bug at review time.

**Nothing about identity changed, so nothing about reproducibility did.** An artifact moved between
deployments, or between tenants by an operator with filesystem access, remains valid and verifiable:
its `artifact_id` still describes its inputs and its `content_id` still describes its bytes
(ADR-0010 §2).

**The billing consequence is visible to operators and should be.** A deployment serving many tenants
with overlapping corpora will pay more than one serving the same documents under one tenant. That is
the cost of §4 and it should appear in an invoice rather than in a surprise.

## Alternatives considered

**One bucket or root per tenant.** Cleaner isolation than a prefix, and rejected on operational
weight: account-level bucket limits are real, provisioning becomes part of tenant creation, and the
filesystem store would need a root per tenant with no way to state that in a single
`DOCDOC_STORE_ROOT`.

**Mix the tenant into the identity derivation.** Would close the oracle by construction, and breaks
ADR-0003. See §2.

**Share the store and scope only at read time.** The cheapest option, and it preserves cross-tenant
reuse — which is a genuine saving. Rejected because the oracle survives it, and the leak is exactly
the fact this system exists to protect: which documents a party holds. Trading a saving that is often
zero for a disclosure that is always available is the wrong direction.

**Flatten timing and always bill as a cache miss over a shared store.** Closes the oracle on paper
while keeping reuse. Rejected as unimplementable honestly: it requires fabricating latency and
recording costs that were not incurred, and any test asserting it would be asserting a fiction.

**Authentication on by default.** The safe default and the wrong one for an upgrade: every existing
deployment would break on the version that introduced it, and pre-1.0 permission to break things
(ADR-0011) is not a reason to spend it here, where the alternative costs a configuration line.
