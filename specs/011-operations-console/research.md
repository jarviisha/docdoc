# Research: The Operations Console

**Feature**: `specs/011-operations-console/` | **Date**: 2026-09-11

Ten questions the spec left to planning, each answered with what was chosen, why, and what was
rejected. Three of them (R1, R2, R6) changed the shape of the milestone rather than filling in a
blank.

---

## R1 — The console's shell cannot be served from behind the credential

**Decision**: the console is mounted at **`/console`**, in its own mount, **without** the API's
credential dependency. Its assets are static HTML, CSS, and JavaScript carrying no tenant data, and
it issues **zero** requests before an operator enters a key.

**Rationale**: this is not a preference, it is forced. `app.py:709` mounts the viewer with the
router's credential dependency and the comment there states the consequence plainly: *"the viewer
cannot send a bearer token, so with authentication on it does not work either way … it does not
load."* A browser's top-level navigation carries no `Authorization` header and cannot be made to.
The console's entire premise — FR-006, and User Story 1 scenario 1 — is that the operator pastes a
key **into a page that has already loaded**. Serving the shell behind the gate makes the console
unusable on precisely the deployments it exists for: the authenticated ones.

So the line moves from *everything is behind the credential* to **the data is behind the credential
and the shell is not**, and the shell is worth nothing without it: no run, no credential record, no
tenant identifier, and no document is reachable from those bytes. This is a deliberate divergence
from `specs/008` FR-059's posture and is recorded in the plan's Complexity Tracking rather than
left to be noticed.

**Alternatives rejected**:

- **Mount the console under `/ui`**, inheriting the viewer's dependency. It would not load. This was
  the original intent and R1 exists because reading the mount killed it.
- **A query-string or fragment credential** to get past the gate on navigation. Forbidden by FR-008
  and for good reason: it lands in history, proxy logs, and referrers.
- **A cookie set by a login route** so navigation carries it. ADR-0019 §4 rejected exactly this, and
  it would reintroduce the four security surfaces the milestone was built to avoid.

---

## R2 — `GET /v1/runs`, and the migration this milestone does not need

**Decision**: keyset pagination over `(created_at, run_id)` descending, `ORDER BY created_at DESC,
run_id DESC`, reusing the existing `runs_by_tenant (tenant_id, created_at)` index. Filter by
`status`, `limit` with a default of **50** and a maximum of **200**. **Zero new migrations.**

The key column is `run_id` and the schema column is `schema_identity` — both written out because
`0001_runs.sql` calls them that, and a plan that says `id` produces a query that says `id`.

**Rationale**: the index Milestone 9 created for retention already covers the access path this route
needs, so the listing costs a query and nothing on disk. Keyset beats `OFFSET` for the reason it
always does — an `OFFSET` page shifts under insertion, which turns FR-018's "every run exactly once"
into a claim that is false under load and true in every test. The `run_id` tiebreak is not covered by the
index; ties in `created_at` are resolved by a sort over the rows sharing one timestamp, which is a
handful.

`# ponytail: sort within equal created_at, add (tenant_id, created_at, run_id) if a tenant ever files
enough runs in one microsecond to matter.`

**Alternatives rejected**:

- **An `OFFSET`/`LIMIT` page.** Cheaper to write, wrong under concurrent submission, and FR-018 is
  the requirement that noticed.
- **A new composite index** to make the tiebreak index-only. A migration bought for a sort over rows
  sharing a microsecond; SC-014's "zero new migrations" is worth more.
- **Reusing `runs_for()`.** It exists for erasure: every state, ascending, no cursor, no status
  filter. Widening it would give one function two callers with opposite needs.

---

## R3 — What a cursor is, and why it is not signed

**Decision**: base64url of a small JSON object holding the tenant, the `created_at`, and the `run_id`
of the last row on the page. Refused when its tenant does not match the caller's.

**Rationale**: FR-016 requires a cursor from another tenant to be refused without disclosing
anything. Carrying the tenant inside the cursor makes that a comparison rather than a lookup. A
signature was considered and is not needed: a forged cursor can only name a position **within the
caller's own tenant**, because the tenant is re-derived from the credential and the query is scoped
by it (FR-014). The worst a caller can do by editing their own cursor is page over their own data
from a different place.

**Alternatives rejected**:

- **An HMAC-signed cursor.** A key to manage and rotate, buying protection against an attack whose
  payoff is "sees their own runs in a different order".
- **An opaque server-side cursor table.** New state, new expiry, new cleanup, in a milestone whose
  claim is one new read and no new state.

---

## R4 — The idle bound is 15 minutes, and it lives in the model

**Decision**: **15 minutes** of no operator interaction discards the credential. The decision is a
pure function in the console's model — `expiredAt(session, now)` — and the shell supplies the clock.

**Rationale**: 15 minutes is the common bound for an administrative surface, short enough to matter
for an unattended tab and long enough that an operator reading a run list does not lose their key
mid-task. It is a number rather than a range because FR-009a requires it be documented as one.

Putting the decision in the model rather than in a `setTimeout` inside a component is what makes
SC-003a testable: under `specs/008`'s clarification the rendering layer carries **no automated
test**, so anything decided inside a component is a requirement with no coverage. The component
ticks; the model decides.

**Alternatives rejected**:

- **No idle bound.** Rejected in clarification: the console erases tenants and the identifier
  FR-030 asks for is on screen.
- **A configurable bound.** Configuration for a value nobody has asked to change, and a second thing
  the documentation has to keep true.
- **A warning countdown before expiry.** A second number and a second UI state; the recovery path is
  "paste the key again", which is cheap.

---

## R5 — The credential rides on the shared transport, and the console gets its own client

**Decision**: `transport.send()` gains an optional credential and sets `Authorization: Bearer`.
The console gets **its own** `client.ts` with its own allow-list; the viewer's is untouched.

**Rationale**: `bearer_of(request.headers.get("authorization"))` is what the API reads, so the header
is settled. The client must be separate because the viewer's exists to prove a different thing: its
`ALLOWED` set holds two paths and `writesToStore()` asserts the viewer never writes. Adding console
intents there would weaken the viewer's guarantee to make the console's code shorter — the exact
trade `check-model-boundary.mjs` exists to prevent. One shared transport keeps FR-041's single
network call site; two clients keep two allow-lists each saying something true.

**Alternatives rejected**:

- **One client for both.** Costs the viewer's two-path allow-list, which is load-bearing for SC-013
  of Milestone 8.
- **A second transport.** Two `fetch` call sites, which is what the boundary check forbids.

---

## R6 — How the console knows authentication is off: it tries

**Decision**: the console offers to continue with no credential. If the first read succeeds, the
deployment is unauthenticated and the console says so on screen. If it is refused, it asks for a key.

**Rationale**: FR-011 requires the console to state which mode it is in, and the obvious
implementation — a route that reports whether authentication is enabled — is a **second new read**,
which costs SC-003. Attempting and observing needs no route at all, and it is also more honest: it
reports what the deployment actually did with a request rather than what a settings endpoint claims
it would do.

**Alternatives rejected**:

- **`GET /v1/auth-mode`** or similar. A second new read, and an unauthenticated oracle describing the
  deployment's security posture.
- **Reading it from the readiness route.** Same disclosure, wearing a route that already exists.

---

## R7 — Administrative areas are discovered the same way

**Decision**: the console probes the credential listing once per session, **with a `tenant_id` that
holds nothing**. A `404` hides every administrative area; a `200` shows them.

**Corrected 2026-09-11 (T072, recorded by T080).** This decision originally read "probes the
credential listing" and named no parameter, and the implementation followed it literally: the route
requires `tenant_id`, answered `422` to the bare call, and the console read that as "not an
administrator" — hiding the credentials and erasure areas from the only principals who can use them.
The research note was as wrong as the code, which is why correcting only the code would have left the
next reader to repeat it.

**Rationale**: ADR-0016 §6 makes administrative routes answer `404` to a non-admin, so "is this key
administrative?" is already answerable with a request the console needs to make anyway. A `/me`-style
route returning the caller's scope would be a second new read and would also be a way to learn which
scopes exist.

**Why a placeholder tenant answers the question honestly.** The console has no way to learn its own
tenant — see R6 for why asking would cost the same thing — so the probe cannot name the caller's.
It does not need to: the scope check precedes the lookup, so a tenant holding nothing produces
`200` with an empty list for an administrator and `404` for everyone else. Nobody's credentials are
read, and the two answers are the whole question.

**Alternatives rejected**:

- **A scope-describing route.** Second new read; see R6.
- **Showing the areas and letting them fail.** An operator clicking into a `404` learns nothing, and
  FR-028 requires the area to be indistinguishable from absent.

---

## R8 — The keyboard claim is checked at the source, because there is nothing else to check it with

**Decision**: a new guard, `ui/scripts/check-console-a11y.mjs`, fails the build on an interactive
handler attached to a non-interactive element without a role, a tab stop, and a key handler, and on a
control with no accessible name. SC-014a is measured by it.

**Rationale**: the rendering layer carries no automated test by decision (`specs/008`), and this
milestone does not add a browser driver — that is a dependency, a CI service, and a class of flake
the repository has so far avoided. A source check is the same instrument `check-readonly.mjs` used
for the same reason: the only inspection available is reading the source, so that is the one that
runs.

`# ponytail: pattern match, not a rendered accessibility tree. It catches the failure that actually
happens — a div with onClick — and not a mislabelled live region. Upgrade path is a browser test,
and it costs a dependency this milestone will not spend.`

**Alternatives rejected**:

- **`axe-core` in a headless browser.** Two dependencies and a CI browser for a milestone claiming no
  conformance level.
- **Reviewing it by eye.** The thing every guard in this repository exists because somebody once did.

---

## R9 — Two builds, because two bases

**Decision**: a second Vite config, `vite.console.config.ts`, with `base: "/console/"` and
`outDir: "dist-console"`. Two npm scripts. The `docdoc-ui` distribution ships both trees.
`docdoc.api.ui` gains a console lookup beside the viewer's, reusing the same three-candidate search.

**Rationale**: Vite's `base` is per-build, and R1 put the console on a different path for a reason
that is not cosmetic. A second config is a file; the alternative — one build serving both — would put
the console under `/ui` and back behind the credential gate that R1 rejected.

**Alternatives rejected**:

- **Multi-page input under one base.** Would work mechanically and puts the console at `/ui/console`,
  which is behind the gate. Dead on R1.
- **Relative asset paths (`base: "./"`)** so one build mounts anywhere. Changes the viewer's build in
  a milestone that promises not to (FR-022b).
- **A separate npm package for the console.** A second dependency tree and a second licence audit for
  code that shares its transport with the viewer.

---

## R10 — Where the console's state lives, and what is tested

**Decision**: all decisions in `ui/src/console/model/` with `node --test` coverage; components hold
rendering only. Python-side: contract tests for the new route, unit tests for cursor encoding and
the status filter, and an integration test over two tenants for SC-007.

**Rationale**: this is the split Milestone 8 established and the reason it gave still holds — the
tested surface is the model, so a decision that drifts into a component loses its coverage silently.
The console has more decisions than the viewer did (idle expiry, credential lifecycle in the page,
admin-area discovery, cursor paging, erasure confirmation matching), which makes the split matter
more rather than less.

**Alternatives rejected**:

- **Testing through the rendered page.** Needs the browser driver R8 declined.
- **Trusting the components.** The erasure confirmation is one `===` away from being decorative; that
  comparison belongs somewhere a test can reach it.
