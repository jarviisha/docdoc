# ADR-0018: Webhook Delivery Semantics, and the Address It Connects To

- **Status**: Accepted
- **Date**: 2026-09-04
- **Implements**: Milestone 10 (`specs/010-operations-and-corrections/spec.md`), FR-053 – FR-066, FR-106
- **Relates to**: [ADR-0013](0013-asynchronous-run-model.md) (at-least-once, and why polling remains the record), [ADR-0010](0010-artifact-store-and-job-model.md) §4 (degradation that fails no run)
- **Principles engaged**: III (Deterministic core, probabilistic edges), IV (Provider-agnostic adapters), XI (MVP discipline), and MVP Scope Constraints §Security

## Context

Milestone 9 made a client poll. That works, it is documented, and it costs a request per interval per
run. Webhooks buy efficiency and latency — not capability — which is why they were deferred and why
they arrive with a constraint: **they must not become the record.**

They also give docdoc something it has never had. Every network call in this project so far has been
*outbound to a provider docdoc chose* — a parser, a model — configured by the operator and named in
an adapter. A webhook is outbound **to an address a caller supplies**, which is a different security
object entirely. A service that fetches a URL on request is a server-side request forgery engine
unless it is built not to be, and it is worth being explicit that this is the first place in docdoc
where that is true.

Three questions follow: what carries the request, how a receiver knows it came from docdoc, and how a
destination is prevented from pointing somewhere it should not.

## Decision

### 1. The standard library carries it, and no dependency is added

`http.client`, `socket`, `hmac`, `hashlib`. **No new extra**, and the `otel` extra this milestone adds
is for the exporter and nothing else.

`httpx` behind a `docdoc[webhooks]` extra was a real option and it loses on §4: the security property
is connecting to an address that has *already been validated*, and the standard library gives that
directly — `HTTPSConnection(host=<ip>, ...)` with the original hostname in `Host` and SNI — while both
high-level clients hide it behind a transport that would have to be subclassed. A security control
implemented by subclassing somebody else's transport is a control that breaks on their next minor
release.

The worker is synchronous, the maintenance tick is synchronous, and a delivery is one POST with a
timeout. There is nothing here that wants an async client.

### 2. At-least-once, one delivery per run, and the constraint is in the schema

`deliveries` carries `UNIQUE (run_id)`. One run produces one delivery row; retries are attempts on
that row. **Two deliveries for one run cannot overlap because there are never two** — ordering is a
database constraint rather than worker discipline.

`delivery_id` is the primary key, allocated once, and is therefore **stable across every retry**. It
is the value a receiver deduplicates on, so an id regenerated per attempt would turn at-least-once
*delivery* into at-least-once *processing* on the receiver's side, which is the one thing
at-least-once is supposed to let them avoid.

Attempts back off and stop at a configured limit, after which the delivery comes to rest at `failed`,
readable by the owning tenant with its attempt count.

### 3. HMAC-SHA256, with the timestamp inside the signed material

```text
X-Docdoc-Signature: t=<unix-seconds>,v1=<hex>
signed material:    f"{t}.{body}"
```

Signing the body alone leaves a captured delivery valid forever. Signing the timestamp with it makes
replay detectable by a receiver that checks skew. The scheme is versioned in the header (`v1=`) so a
second one can exist later without ambiguity about which was used.

Symmetric, because the receiver population per destination is one and the secret is registered by the
operator who owns both ends. Ed25519 would handle key distribution better and there is no key
distribution problem to handle; TLS client certificates move the problem into infrastructure and
cannot be tested offline.

### 4. Resolve, validate every address, then **pin** — and re-validate before each attempt

1. `getaddrinfo` the host.
2. Refuse if **any** resolved address is loopback, link-local, private, multicast, reserved, or
   unspecified. Every address, not the first: a host that resolves to one public and one private
   address *is* the attack.
3. Connect **to the validated address**, presenting the original hostname in `Host` and in TLS SNI.
4. **Do not follow redirects.** A public URL answering `302 → 169.254.169.254` is the other half of
   the same attack.

Non-HTTPS destinations are refused unless a deployment explicitly allows them.

**Step 4 of the process — re-validating before every attempt, not only at registration — is what
closes DNS rebinding.** Validating a hostname and then handing it to a client that resolves it again
is a time-of-check-to-time-of-use bug with a name, and FR-060 requires validation "again before each
attempt" for exactly this reason.

The `422` at registration names the *class* of refusal and **not the addresses it resolved to**, which
would make the route a resolver for an unauthenticated caller.

An allowlist of destination hosts is safest and unusable for a product whose customers each have
their own endpoint; it remains available as the explicit-allowance configuration, for a deployment
that wants it.

### 5. The payload carries identities and no content

Run identity, terminal state, failing stage and error class where the run failed, processing identity
where one exists, routing outcome where a policy is configured, and the delivery identifier.

No document text, no extracted value, no claimed text, no prompt body, no credential, no provider
message. The same rule every observer in this project follows, applied to the one surface that sends
data to a third party the operator configured rather than to a log the operator reads.

`last_error` on the delivery row holds a **class name**, never the receiver's response body. A
receiver's body is somebody else's content and docdoc has no business storing it.

### 6. Delivery changes no run state, and polling remains the record

A failing receiver does not fail a run, does not block a worker, and does not delay another tenant's
deliveries. A run's terminal state is readable by polling exactly as it was in Milestone 9, so **a
lost delivery loses no information**.

This is ADR-0010 §4's degradation rule applied to a new surface rather than a new behaviour invented
for it: the thing that cannot be reached is worked around, once, and logged.

### 7. Attempts happen in the worker's maintenance tick, bounded

There is no delivery process. The tick performs due deliveries between claims, bounded **between
items and never within one** — a delivery has its own timeout, and cutting it mid-flight produces an
attempt nobody can classify. The worst case a worker's next claim waits is one tick budget plus one
delivery timeout, and that is the number the operator documentation states.

No thread, no subprocess, no event loop: Milestone 9's FR-025 forbids them in the worker, and its
reason — a GIL-holding stage delaying a sibling's heartbeat into losing a lease it still holds —
reaches maintenance identically.

## Consequences

**docdoc can now be pointed at a URL, and the guard is in code rather than in advice.** The
destination policy is the security boundary of this ADR; if it is weakened, the feature becomes a
request-forgery service with a signature header.

**A deployment that registers no callbacks opens no socket at all**, asserted by a test that patches
`socket.socket` to raise and requires a full run to succeed. The `forbidden` import contract confines
the network to `docdoc.runs.delivery` and `docdoc.telemetry` and nowhere else, so a future module
cannot quietly acquire the capability.

**Receivers must deduplicate**, and are given the means to. At-least-once is the honest guarantee: a
delivery that succeeded but whose acknowledgement was lost will arrive again, carrying the same
`delivery_id`.

**Deliveries stop at the attempt limit and stay stopped.** There is no dead-letter replay and no
manual redelivery route; a client whose receiver was broken for a day reads the run state by polling,
which is why §6 matters more than it looks.
