# Feature Specification: The Operations Console

**Feature Branch**: `011-operations-console`

**Created**: 2026-09-11

**Status**: Implemented

<!--
  Status vocabulary, and who moves it. Every spec in this repository read "Draft"
  through Milestone 5 — including five that had shipped and merged — because
  nothing ever moved it. A field nobody maintains is a field that lies, so the
  transitions are named here and each one is owned by a step that already runs.

    Draft        still being written; /speckit-clarify may still change answers
    Accepted     clarified, planned, and tasked; implementation not started
    Implemented  the behaviour this spec describes exists and is merged
    Superseded by NNN-name   replaced by a later spec

  Draft -> Accepted is manual, done when tasks.md is first produced. Nothing
  enforces it. Accepted -> Implemented is wired: every milestone's task list
  carries a README-roadmap task, and that task now flips this field too, so the
  transition that matters most rides on a step the milestone already has to do.
-->

**Input**: User description: "Milestone 11 — the operations console. A browser surface for the person
who runs a docdoc deployment, governed by ADR-0019 and permitted by constitution v1.9.0: list and
inspect runs, issue and revoke credentials, trigger tenant and document erasure. Every write goes
through a Milestone 10 route that already exists; the only new server-side concept is one
tenant-scoped read. The key is pasted and held in memory, never persisted. Out of scope: recording
corrections, and the five forbidden nouns."

## What Milestone 11 is, and the sentence that permits it

Milestone 10 built retention, credential lifecycle, limits, delivery, tracing, routing, and
corrections, and then said plainly that it built no interface for any of it: *"an operator or
administrative interface … This milestone makes such an interface **possible** by giving credentials
a lifecycle; it does not build one."* This is that interface.

It is permitted by one paragraph and bounded by it. Constitution **v1.9.0** distinguishes a **review
platform** — the deferred thing — from an **operations console**, and the distinction is not
technological. Both are browser surfaces made of the same parts. The line is **who the surface is
about**:

- about the person who **runs the deployment** — what ran, what is held, which credentials exist,
  what was delivered. Permitted.
- about the person who **checks documents** — assignment, queues, workload, review state,
  disposition. Deferred, by Principle IX's own sentence that supporting corrections "MUST NOT turn
  the MVP into a workflow or review platform".

**ADR-0019** is the decision record; this spec is what it decided, stated as requirements.

## It adds no capability, and that is the claim it is measured on

Every administrative action here already exists and already has tests. `docdoc credential
issue|revoke|list`, `docdoc erase`, `docdoc sweep` and the Milestone 10 routes behind them are what
an operator uses today over SSH. The console is a **face on work that is already done**, and its
entire server-side footprint is **one new read**: a tenant-scoped list of runs, because
`GET /v1/runs/{run_id}` answers only for an identifier the caller already holds, and a surface that
cannot enumerate cannot open.

That is the milestone's measurable claim, in the shape Milestones 9 and 10 each used: golden-set
metrics are bit-identical, no deterministic layer is touched, and a deployment that never serves the
console observes nothing new. A milestone whose headline is an interface is the easiest place to
smuggle in a behaviour change, so the same instrument is kept: a test reads the diff.

## The credential problem, and the mechanism deliberately not built

A browser has no credential. `specs/010` named this as the reason an administrative interface was
deferred: *"It needs a session mechanism the viewer does not have."*

The mechanism this milestone builds is the smallest one that is honest about it: **the operator
pastes the key they already have, it lives in the page for as long as the page does, and a reload
asks again.** No login route, no session table, no cookie, no CSRF token, no expiry policy.

The alternative was considered and rejected in ADR-0019 §4: a cookie session is four new security
surfaces written by this project to guard a credential the deployment already has and already rotates
through ADR-0016. Issuing a second credential type to protect the first is the thing that would need
justifying, not the decision to skip it.

The consequence is stated rather than softened: **a reload loses the key.** That is the same cost the
viewer already pays for the document under `specs/008` FR-032, and the guard that enforces it —
no `localStorage`, no `sessionStorage`, no IndexedDB, no Cache API anywhere in `ui/src` — is already
in this tree and already failing builds. Nothing new is needed to make the key obey it.

A deployment wanting single sign-on puts an authenticating proxy in front of the console. That is the
operator's infrastructure and docdoc ships no part of it.

## The fence, in five nouns and fifteen words

Milestone 8's fence was *it renders and never edits*, and a script has been re-checking it ever
since. That fence cannot be reused: a console that revokes a credential edits by definition. So the
fence moves from **what it does** to **what it is about**, and it is stated as identifiers rather
than as a principle, because a principle is argued at review time and an identifier is grepped.

Five nouns the console may not hold, display, or let anyone set: **reviewer assignment**, **a review
queue**, **workload**, **a review state**, **a disposition** (ADR-0019 §2).

Fifteen words no route may contain, already enforced by `tests/contract/test_no_review_platform.py`
and left unchanged by this milestone: `assign`, `assignment`, `reviewer`, `reviewers`, `queue`,
`worklist`, `work-list`, `inbox`, `task`, `tasks`, `approve`, `approval`, `reject`, `escalate`.

This costs something real and the cost is recorded rather than discovered later: an operations
console genuinely wants to show how much work is waiting for the workers, and the obvious name for
that contains `queue`. It gets a different name, or it is not built. Widening the test to "one of
these words **plus** a noun about a person" was rejected in ADR-0019 §3 — a guard that has started
reasoning is one a future route can argue its way past, and a word list either matches or does not,
which is the only property that makes it still true in two years.

## Clarifications

### Session 2026-09-11

- Q: How does the browser authenticate against the API? → A: The operator pastes an API key; it is
  held in memory for the life of the page and is never persisted. No login route and no cookie
  session (ADR-0019 §4).
- Q: Does this milestone include a surface for recording corrections? → A: No. The route exists
  (`POST /v1/runs/{run_id}/corrections`) and is deliberately left without a face here; ADR-0019 §6
  leaves which milestone builds it open, and requires that milestone to say which side of the §1 line
  it lands on.
- Q: Does the console get a cross-tenant view for administrative credentials? → A: Not in this
  milestone. It would be a second new read, and "one new server-side concept" is the claim this
  milestone is measured on. The console shows the tenant its key resolves to.
- Q: How much of a run's result does the run detail show? → A: None of it. The console links to the
  result representation the API already serves, and renders no extracted value, claimed text, or
  document text itself. `GET /v1/runs/{run_id}` already holds this line — *"One result
  representation, reachable one way"* — and a console that rendered values would be the second one.
  It also keeps FR-037 absolute, which is what makes SC-016 a single check rather than a list of
  exceptions.
- Q: Does that link reach the Milestone 8 viewer, so an operator can see a stored run's grounding
  overlay? → A: No. The viewer holds a hard allow-list of two paths (`/v1/schemas`, `/v1/extract`)
  and has no way to open a stored run; giving it one needs a route serving a stored document's
  bytes, which `GET /v1/documents/{blob_id}` does not do — it returns metadata. That would be a
  **second** new read, and "exactly one new server-side concept" is the claim this milestone is
  measured on by SC-003. The link goes to the result representation only, and opening a stored run in
  the viewer is deferred with its cost recorded.
- Q: Does the in-memory credential survive an idle tab? → A: No. It is discarded after a documented
  bound of inactivity, and the console then asks for it again through the path FR-010 already
  defines. This is the one place in this milestone where the smaller mechanism was rejected: the
  console can revoke credentials and erase a tenant, the tenant's identifier is on screen for anyone
  to copy into FR-030's confirmation, and "the operator locks their laptop" is not a control this
  project can assert. The bound is a documented, observable number; the number itself belongs to
  `/speckit-plan`, as `specs/010` FR-028's propagation bound did.
- Q: What does the console commit to on accessibility? → A: The stance `specs/008` FR-059 set for the
  viewer, unchanged: every control labelled and operable by keyboard, built from semantic elements,
  and **no conformance level claimed** — with the documentation saying so rather than leaving a
  reader to assume one. Two applications in one tree speaking with two voices on this would be worse
  than either voice. The keyboard half is not negotiable here even though the claim is modest: a
  confirmation dialog that erases a tenant and can only be reached with a mouse is a trap.
- Q: What happens when an operator revokes the credential they are currently using? → A: It is
  allowed, preceded by one general warning that a revocation may include the key in use and will then
  require entering another. The console does **not** identify which listed credential is its own:
  doing so needs a route returning the caller's credential identity, which is a second new read and
  costs SC-003. Refusing the action was rejected for a second reason — it would be the console
  inventing a rule the API does not have, and blocking the emergency the feature exists for. The
  aftermath is FR-010 unchanged: the next action is refused, the key is discarded, the console asks
  again.
- Q: Where does the console live relative to the Milestone 8 viewer? → A: The same application tree,
  a sibling source directory, a separate entry point. The viewer's read-only guard is re-pointed at
  the viewer's own sources so its claim stays machine-checked rather than inherited by a tree that
  now contains forms (ADR-0019 §5).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get in with the key you already have, and leave nothing behind (Priority: P1)

An operator opens the console on a laptop that is not theirs — a colleague's, a shared machine in an
office, a support session. They paste the key the deployment issued them, do the work, and close the
tab. Nothing they did is recoverable from that browser afterwards: no key in storage, no document, no
cached run.

**Why this priority**: P1 because nothing else in this milestone is reachable without it, and because
getting it wrong is the one failure here that is a security incident rather than an inconvenience.

**Independent Test**: Paste a key, perform an authenticated read, reload the page, and confirm the
console asks again; then inspect every browser storage mechanism and find nothing.

**Acceptance Scenarios**:

1. **Given** a deployment with authentication enabled, **When** the operator opens the console with
   no key entered, **Then** they are asked for one and no authenticated request has been attempted.
2. **Given** a valid key pasted, **When** the operator performs any read, **Then** it succeeds and
   the key is sent as a credential and never as part of a URL.
3. **Given** a working session, **When** the page is reloaded, **Then** the key is gone and the
   console asks for it again.
4. **Given** any point in a session, **When** browser storage is inspected, **Then** the key, the
   documents, and the run data are absent from all of it.
5. **Given** a key that was revoked while the tab was open, **When** the next action is taken,
   **Then** the console reports that the credential is no longer accepted and asks for another,
   rather than showing a generic failure.
6. **Given** a session left untouched past the documented idle bound, **When** the operator returns
   and acts, **Then** the key has already been discarded, the console asks for it again, and no
   request was made with the discarded key.
7. **Given** a deployment with authentication disabled — the Milestone 9 default, where every route
   behaves as it did before credentials existed — **When** the console is opened, **Then** it works
   without a key and says which mode it is in, rather than demanding one it cannot check.

---

### User Story 2 - See what ran, without opening a shell on the server (Priority: P1)

An operator is asked why a customer's document "never came back". Today the answer requires a
terminal, a database, and an identifier the customer may not have. In the console they open the list
of runs, narrow it to the failures, and read the one that matters: when it was submitted, what state
it reached, what the routing policy decided, and whether the result was delivered.

**Why this priority**: P1 because it is the only story requiring a new server-side read, so every
other story can be built on top of it, and because "what ran?" is the question an operator asks most
and currently answers least easily.

**Independent Test**: Submit runs that succeed, fail, and are cancelled, then confirm each appears in
the list, that filtering by state returns exactly the matching ones, and that a second tenant's runs
appear in neither.

**Acceptance Scenarios**:

1. **Given** runs under the operator's tenant, **When** the list is opened, **Then** it shows them
   most-recent-first with their state, submission time, and identity.
2. **Given** more runs than one page holds, **When** the operator pages forward and back, **Then**
   every run is reachable exactly once and no run is skipped or repeated.
3. **Given** runs in several states, **When** the operator filters by one, **Then** exactly the runs
   in that state are shown.
4. **Given** a run belonging to another tenant, **When** the list is opened with this tenant's key,
   **Then** that run does not appear and cannot be reached by typing its identity.
5. **Given** a succeeded run, **When** it is opened, **Then** its state, timings, routing outcome if
   a policy is configured, and a way to reach its result are shown; and when no policy is configured,
   no routing section is shown rather than an empty one.
6. **Given** a run that was erased or aged out, **When** the operator opens it, **Then** they are
   told it was removed, when, and under which policy — and nothing else about what it held.
7. **Given** a failed run, **When** it is opened, **Then** the failure is reported in the typed,
   provider-neutral form the API already produces, with no document text in it.

---

### User Story 3 - Revoke a leaked key from wherever you happen to be (Priority: P1)

A key appears in a public repository at 23:00. The operator opens the console on a phone, finds the
credential, revokes it, issues a replacement, and hands it to the service owner. No SSH, no laptop,
no deployment.

**Why this priority**: P1 because it is the action whose value is most destroyed by delay, and
because it is the one an operator is most likely to need while away from a terminal.

**Independent Test**: Issue a credential through the console, use it, revoke it through the console,
and confirm it stops being accepted within the documented propagation bound.

**Acceptance Scenarios**:

1. **Given** an administrative key, **When** the operator lists credentials, **Then** each is shown
   by identity, tenant, scope, issue time, and revocation state — and never as a key or a fragment
   of one.
2. **Given** a new credential issued through the console, **When** it is created, **Then** the key is
   shown exactly once, with the warning that it cannot be shown again, and it is not present in the
   page afterwards.
3. **Given** a credential, **When** it is revoked, **Then** the list reflects it immediately and the
   credential stops being accepted within the bound ADR-0016 §3 documents.
4. **Given** a credential already revoked, **When** it is revoked again, **Then** the operation
   succeeds and nothing changes.
5. **Given** the operator revokes the credential they are holding, **When** the next action is taken,
   **Then** it is refused as unauthenticated, the console discards the key and asks for another, and
   nothing reports this as an error in the revocation.
6. **Given** a key **without** the administrative scope, **When** the credentials area is requested,
   **Then** the response is indistinguishable from that area not existing — never a message saying
   permission was denied (ADR-0016 §6).

---

### User Story 4 - Erase a customer's data, on purpose and not by accident (Priority: P1)

A customer exercises a right and the operator has a deadline that is not theirs. They erase the
tenant — or one document inside it — from the console, and the deployment reports what it removed.
The screen makes an accidental erasure hard: the target is typed back, not clicked once.

**Why this priority**: P1 because it is the action with the largest blast radius in the entire
product, and because the confirmation design is the whole feature. Making it available without making
it deliberate would be a net loss.

**Independent Test**: Erase a tenant through the console with a second tenant holding byte-identical
documents, and confirm the second tenant's runs still resolve and still reuse their artifacts.

**Acceptance Scenarios**:

1. **Given** an erasure target, **When** the operator asks to erase it, **Then** they must re-enter
   the identifier before the action is offered, and the action is refused while the typed value
   differs by any character.
2. **Given** a confirmed erasure, **When** it completes, **Then** the counts of what was removed are
   shown as the deployment reported them.
3. **Given** an erasure of a tenant that does not exist, **When** it is confirmed, **Then** it
   succeeds having removed nothing, rather than failing.
4. **Given** an erased run, **When** it is opened afterwards, **Then** it reports as removed with its
   policy and timestamp.
5. **Given** a key without the administrative scope, **When** the erasure area is requested, **Then**
   it is indistinguishable from that area not existing.

---

### User Story 5 - Find out why a callback never arrived (Priority: P2)

A client says they were never told a run finished. The operator opens the run and reads the delivery
record: whether a destination was registered, how many attempts were made, what each attempt was
answered with, and whether the delivery has come to rest.

**Why this priority**: P2 because it is a diagnosis rather than an action, and because Milestone 10
already records everything needed — this only stops the record being unreachable without a database
client.

**Independent Test**: Register a destination that fails every attempt, run a document through, and
confirm the console shows the attempts and the final at-rest state.

**Acceptance Scenarios**:

1. **Given** a run with a registered destination, **When** its delivery is opened, **Then** the
   attempts, their outcomes, and the current state are shown.
2. **Given** a run with no destination registered, **When** its delivery is opened, **Then** it says
   so plainly rather than showing an empty table.
3. **Given** any delivery view, **When** it is rendered, **Then** no signing secret and no fragment
   of one appears in it.

---

### Edge Cases

- **The key is wrong, or has a trailing newline from the clipboard.** The console reports that the
  credential was not accepted and asks again. It does not retry, and it does not report a transport
  failure as a rejected credential — the two are already distinguished in this application's model
  and that distinction is kept.
- **The key is revoked mid-session.** The next action fails as unauthenticated; the console drops the
  key it holds and asks for another, so the operator is not left clicking a dead page.
- **Authentication is disabled.** Every route behaves as it did in Milestone 8. The console works
  with no key, and says which mode it is in. It must not present a key field that checks nothing.
- **A tenant with no runs.** An empty list says the tenant has no runs, not "no results found" over a
  filter nobody set.
- **A cursor that is stale, malformed, or from another tenant.** The list refuses it and returns to
  the first page rather than showing another tenant's position in a sequence.
- **A run erased between the list being drawn and the detail being opened.** The detail reports the
  removal; the stale row is not treated as a contradiction.
- **A `410` and a `404` mean different things and are shown differently** — *it was yours and it is
  gone* versus *there is nothing here* — and the second is what every other tenant sees for both.
- **The deployment has no database configured.** The console reports that runs are not available in
  this deployment rather than presenting an empty list that looks like "no work has been submitted".
- **A browser with JavaScript disabled or an asset that fails to load.** The page says the console
  could not start. It does not silently render a shell with no data, which reads as "nothing to show".
- **A tab left open and unattended.** The key is discarded at the idle bound, and a half-completed
  erasure confirmation goes with it — the confirmation is per-action state, not something the console
  holds across a re-authentication.
- **Two tabs, two different keys.** Each tab holds its own; nothing is shared, because nothing is
  stored. Each idles independently, because activity in one is not activity in the other.
- **An operator pastes a key into the erasure confirmation field by muscle memory.** The confirmation
  compares against the target identifier, so this fails closed.

## Requirements *(mandatory)*

### Functional Requirements

#### The surface, and what it is about

- **FR-001**: The system MUST provide a browser console for the person who operates a deployment,
  served by the existing API process as static assets, at **`/console`** — a path distinct from the
  Milestone 8 viewer's, and one that contains none of the fifteen words FR-003 names.
- **FR-002**: The console MUST NOT hold, display, or allow the setting of any of: reviewer
  assignment, a review queue, workload, a review state, or a disposition (ADR-0019 §2). This MUST be
  enforced by a build-time check over the console's sources, in the manner `check-readonly.mjs`
  enforces Milestone 8's claim, and not by review alone.
- **FR-003**: No route added by this milestone may contain any of the fifteen words
  `tests/contract/test_no_review_platform.py` forbids in a path. That test MUST NOT be modified,
  widened, or exempted by this milestone.
- **FR-004**: `RunStatus` MUST gain zero members and the documented run state machine MUST gain zero
  transitions. The console displays the five states Milestone 9 closed the set at and defines no
  sixth, including for removed runs, which are reported as no longer being runs (`specs/010`
  FR-004a).
- **FR-005**: The console MUST be optional. A deployment that does not serve it MUST behave exactly
  as a Milestone 10 deployment does, and the API MUST start and operate with the console's assets
  absent.

#### The credential, and the session that is not one

- **FR-006**: The console MUST authenticate by a credential the operator supplies at the start of a
  session, entered into the page.
- **FR-007**: The credential MUST be held in the page's memory for the life of that page and MUST NOT
  be written to `localStorage`, `sessionStorage`, IndexedDB, the Cache API, a cookie, or any other
  persistent or shared browser mechanism. The existing no-persistence guard MUST cover the console's
  sources.
- **FR-008**: The credential MUST be sent as a request credential and MUST NOT appear in a URL, a
  query parameter, a fragment, a page title, or any value that reaches a browser history, a proxy
  log, or a referrer header.
- **FR-009**: The console MUST NOT introduce a login route, a session record, a session cookie, a
  token exchange, or an expiry policy. No server-side state describing a console user may exist.
- **FR-009a**: The console MUST discard the credential it holds after a bounded period of operator
  inactivity, and MUST then behave exactly as it does before a credential is entered. The bound MUST
  be documented as a number and MUST be observable in a test. It is an idle bound and not a session
  expiry: nothing server-side records it, and nothing is revoked by it.
- **FR-010**: On a response indicating the credential was not accepted, the console MUST discard the
  credential it holds and ask for another, distinguishing that case from a transport failure.
- **FR-011**: The console MUST operate against a deployment with authentication disabled without a
  credential, and MUST state which mode it is in rather than presenting a field that checks nothing.
- **FR-012**: The console MUST NOT display a credential, or any fragment of one, anywhere except the
  single post-issuance disclosure of FR-024.

#### Listing runs — the one new read

- **FR-013**: The system MUST provide a route that lists the calling principal's runs. This is the
  only new server-side read this milestone introduces.
- **FR-014**: The listing MUST be scoped to the caller's tenant **as a predicate in the query**, per
  ADR-0014 §3, so that another tenant's runs are absent for the same reason an unknown run is absent
  and the two cannot drift into two behaviours.
- **FR-015**: The listing MUST be pageable by an opaque cursor and MUST accept a bounded page size,
  with a documented default and a documented maximum.
- **FR-016**: A cursor that is malformed, expired, or issued for another tenant MUST be refused
  without disclosing anything about the sequence it referred to.
- **FR-017**: The listing MUST support filtering by run state, using exactly the five members of the
  closed status set.
- **FR-018**: The listing MUST order results deterministically, so that paging over an unchanging
  data set returns every run exactly once.
- **FR-019**: A run's list entry MUST carry its identity, state, submission time, and terminal time
  where one exists. It MUST NOT carry extracted values, claimed text, document text, or a schema's
  contents.
- **FR-020**: The listing MUST NOT include runs that have been removed. A removed run is reachable
  only by its identity, and only by its owner, as `specs/010` FR-011 already defines.
- **FR-021**: The listing MUST be available to a principal with no administrative scope, for that
  principal's own tenant. Listing across tenants is out of scope for this milestone.

#### What the console shows about a run

- **FR-022**: The console MUST present a run's state, its timings, its routing outcome where a policy
  is configured, its delivery record where a destination is registered, and a way to reach its
  result. Where no routing policy is configured, no routing section may be shown — absence, not an
  empty section, matching the API's own decision to omit the field rather than send null.
- **FR-022a**: "A way to reach its result" is a **link and nothing more**, and it addresses the
  result representation the API already serves. The console MUST NOT render a result's
  values, claimed text, grounding, or validation findings, and MUST NOT derive a count, a rate, or a
  summary from any of them. `GET /v1/runs/{run_id}` already fixes this — one result representation,
  reachable one way — and a console that rendered any part of a result would be the second, with its
  own rounding, its own field ordering, and its own opportunity to disagree with the first.
- **FR-022b**: The Milestone 8 viewer MUST NOT be modified by this milestone. Its request allow-list
  keeps exactly the two paths it holds, and it gains no ability to open a stored run. A console link
  into it MUST NOT be offered, because there is nothing on the other side of one.
- **FR-023**: For a removed run, the console MUST show the removal time and the policy under which it
  was removed, and nothing else about what it held.

#### Credentials

- **FR-024**: The console MUST allow a principal holding the administrative scope to issue a
  credential, and MUST display the issued key exactly once, stating that it will not be shown again.
- **FR-025**: The console MUST allow listing credentials by identity, tenant, scope, issuance time,
  and revocation state — never by key or key fragment.
- **FR-026**: The console MUST allow revoking a credential, and revocation MUST be idempotent.
- **FR-026a**: Revoking the credential currently in use MUST be permitted. The console MUST warn,
  before the first revocation of a session, that a revocation may include the key in use and will
  then require another to be entered; it MUST NOT identify which listed credential is its own, and
  MUST NOT refuse or hide a revocation on that basis. The aftermath is FR-010: the next action is
  refused, the credential is discarded, and the console asks for another.
- **FR-027**: Every credential operation MUST go through the routes Milestone 10 already ships. This
  milestone MUST NOT add a credential route, alter a credential's storage, or introduce a second
  issuance path.
- **FR-028**: To a principal without the administrative scope, every administrative area of the
  console and every administrative route MUST be indistinguishable from not existing, per ADR-0016
  §6. A response that confirms the area exists but denies access MUST NOT be produced.

#### Erasure

- **FR-029**: The console MUST allow erasure of a tenant and erasure of a document, through the
  routes Milestone 10 already ships.
- **FR-030**: An erasure MUST require the operator to re-enter the target identifier, and MUST be
  unavailable while the entered value differs from the target by any character.
- **FR-031**: An erasure MUST report the counts the deployment returned, as it returned them, without
  the console computing, summing, or estimating a figure of its own.
- **FR-032**: An erasure of a target that does not exist MUST be reported as having succeeded and
  removed nothing, matching the route's own idempotent behaviour.
- **FR-033**: The console MUST NOT offer bulk erasure, multi-select erasure, or any action that
  erases more than one named target per confirmation.

#### Delivery

- **FR-034**: The console MUST show, for a run, whether a destination was registered, the attempts
  made, their outcomes, and whether delivery has come to rest.
- **FR-035**: No signing secret, and no fragment of one, may appear in any delivery view.
- **FR-036**: The console MUST NOT offer re-delivery, cancellation of a delivery, or editing of a
  destination's address in this milestone.

#### What must not leak

- **FR-037**: No view in the console may display document text, extracted values, or claimed text.
  There is no exception; the viewer is a separate application showing a document its own user
  selected, and the console neither embeds it nor links into it (FR-022a, FR-022b).
- **FR-038**: The console MUST NOT log, and MUST NOT cause the API to log, a credential, a document,
  a value, or a prompt. The constitution's security rule is unchanged and this milestone adds no
  exception to it.
- **FR-039**: An error reaching the operator MUST be the typed, provider-neutral error the API
  already produces. The console MUST NOT invent an error vocabulary, and MUST NOT present a transport
  failure as a deployment-reported failure.

#### The fences that must keep holding

- **FR-040**: Milestone 8's read-only guarantee MUST remain machine-checked over the viewer's own
  sources. The guard MUST be re-pointed rather than relaxed, exempted, or deleted, and the viewer
  MUST gain no write path (ADR-0019 §5, `specs/010` FR-084).
- **FR-041**: The console MUST construct every request through the single tested request-construction
  point the application already has, and no component may call the network directly. The existing
  model-boundary check MUST cover the console's sources.
- **FR-042**: This milestone MUST touch zero files under the kernel, ingest, extraction, grounding,
  and validation layers, and MUST NOT alter any identity derivation. A test MUST assert this by
  reading the diff, as Milestone 10's does.
- **FR-043**: This milestone MUST add no new runtime dependency to the base install, and no new
  server-side process, container, or scheduled job.
- **FR-044**: The offline test suite MUST pass with no database, no object store, and no browser
  present.
- **FR-045**: Every licence obligation of any front-end dependency MUST be recorded by the existing
  licence check, which MUST cover the console's dependencies.

#### Accessibility

- **FR-045a**: Every control the console offers MUST carry an accessible name and MUST be operable by
  keyboard alone, including the erasure confirmation and the credential entry. A control reachable
  only by pointer MUST NOT exist.
- **FR-045b**: The console MUST be built from semantic elements and MUST convey state changes —
  a list loading, an erasure completing, a credential being refused — to assistive technology rather
  than by visual means alone, as the viewer's in-flight status already does.
- **FR-045c**: No accessibility conformance level is claimed, and the documentation MUST state that
  no level is claimed (`specs/008` FR-059, restated rather than assumed to carry over).

#### Documentation

- **FR-046**: The README roadmap MUST gain a Milestone 11 row, and it MUST state what the console
  does **not** do as plainly as what it does — specifically that it is not a review platform and
  holds no per-reviewer state.
- **FR-047**: The documentation MUST state that a reload requires the key to be re-entered, and that
  an idle session discards it after the documented bound — both as designed consequences with their
  reasons, not as limitations to be fixed later, and the bound MUST be stated as a number.
- **FR-048**: The documentation MUST state that a deployment wanting single sign-on places an
  authenticating proxy in front of the console, and that docdoc ships no part of one.

### Key Entities

- **Console session**: the credential and the tenant it resolved to, held in one page for the life of
  that page. It has no identifier, no server-side record, and no expiry beyond the page's own.
- **Run summary**: one row in the listing — identity, state, submission time, terminal time. A
  projection of a run that already exists, carrying no value, text, or schema content.
- **Page cursor**: an opaque position in a deterministic ordering, scoped to the tenant that was
  issued it.
- **Credential record**: identity, tenant, scope, issuance time, revocation state. Milestone 10's
  entity, displayed and not redefined. The key itself is not part of it.
- **Erasure request**: a named target and the operator's re-entry of that name. Not persisted; it
  exists for the duration of one confirmation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Golden-set metrics are **bit-identical** before and after this milestone, and a result
  produced by a deployment serving the console agrees with one produced by a deployment that does not
  on **100%** of values, verdicts, locations, and identities.
- **SC-002**: The diff for this milestone touches **zero** files under the kernel, ingest,
  extraction, grounding, and validation layers, and **zero** lines of any identity derivation.
- **SC-003**: This milestone adds exactly **one** new API route beyond those Milestone 10 ships, and
  it is a read. **100%** of the console's writes reach routes that existed before this milestone.
- **SC-003a**: After the documented idle bound elapses with no operator activity, **100%** of
  subsequent actions require the credential to be entered again, and **0** requests are made with the
  discarded one.
- **SC-004**: **0** bytes of credential, document, value, or run data are recoverable from any
  browser storage mechanism after a console session, verified by inspecting every mechanism the
  existing guard names.
- **SC-005**: A credential appears in **0%** of URLs, query strings, fragments, browser history
  entries, referrer headers, and server log lines, verified over a credential seeded with a
  distinctive string.
- **SC-006**: **0** of the fifteen forbidden words appear in any route path, and **0** of the five
  forbidden nouns appear in the console's sources, each asserted by a check that fails the build.
- **SC-007**: A principal of tenant A sees **0** of tenant B's runs through the listing, through any
  cursor, and through any filter — verified with both tenants holding byte-identical documents.
- **SC-008**: A non-administrative principal receives responses byte-identical to those for a route
  that does not exist on **100%** of administrative routes, and **0%** of responses distinguish
  "forbidden" from "absent".
- **SC-009**: Paging over an unchanging set of runs returns **100%** of them exactly once, with
  **0** duplicates and **0** omissions, verified over a set larger than one page.
- **SC-010**: A revoked credential stops being accepted by the console within the propagation bound
  ADR-0016 §3 documents, in **100%** of attempts, with **zero** process restarts.
- **SC-011**: An erasure is impossible to trigger without re-entering the exact target: **0%** of
  attempts with a differing confirmation proceed.
- **SC-012**: The Milestone 8 viewer's read-only check still passes and still covers **100%** of the
  viewer's sources, with **0** exemptions added by this milestone.
- **SC-013**: A Milestone 10 deployment upgrades with **zero** configuration changes and observes
  **zero** behavioural differences when it does not serve the console.
- **SC-014**: The base install acquires **zero** new runtime dependencies, the deployment still has
  **four** process types and **four** containers, and the offline suite passes with no database, no
  object store, and no browser.
- **SC-014a**: **100%** of the console's controls — credential entry, list filters, paging, credential
  issuance and revocation, and the erasure confirmation — are reachable and operable by keyboard
  alone, and **100%** carry an accessible name. **Zero** accessibility conformance levels are
  claimed, and the documentation says so.
- **SC-015**: An operator who has never seen the console can, from a key and a URL alone, find a
  named failed run and state why it failed in under **3 minutes**, without consulting documentation.
- **SC-016**: **0%** of console views — the run detail included — display document text, extracted
  values, claimed text, grounding, or validation findings, and **0** figures shown anywhere in the
  console are derived from a result's contents. Asserted as one check with no exception list.
- **SC-017**: With authentication disabled, **100%** of console functionality that a Milestone 10
  deployment exposes unauthenticated is reachable with no credential entered, and the mode is stated
  on screen.

## Assumptions

- The operator already holds a credential. Bootstrapping the first administrative credential remains
  a command-line act (ADR-0016 §5) and this milestone does not change it.
- One tenant per session is sufficient, because one principal resolves to exactly one tenant
  (`specs/009` FR-060). An operator working for two customers holds two keys and opens two tabs.
- The console is served over a channel the operator trusts. Transport security is the deployment's,
  as it already is for every other route.
- Losing the view on reload is acceptable to the operator, as it already is in the viewer.
- Modern browser only, matching the viewer's existing assumption. No browser support matrix is
  widened by this milestone.
- The existing front-end toolchain, component library, and test runner are sufficient; this milestone
  is not a place to change any of them.

## Dependencies

- **Constitution v1.9.0 is a dependency of implementation**, not of planning. Without its
  distinction between an operations console and a review platform, gate 12 fails and no code task
  begins. It was adopted 2026-09-11, in the same change as ADR-0019.
- **ADR-0019 is the decision record** for the boundary, the credential handling, the single new read,
  and the route-naming constraint. This spec states what it decided and does not reopen it.
- **Milestone 10's routes are the entire write surface**: credential issuance, listing and
  revocation; tenant and document erasure; delivery records; routing outcomes. None is redefined.
- **ADR-0014 §3's query-predicate tenant scoping** is what the new listing must follow; the existence
  oracle stays closed by construction rather than by a check after the fetch.
- **ADR-0016 §3 and §6** fix the revocation propagation bound and the `404`-not-`403` rule the
  console inherits.
- **`specs/008` FR-029 and FR-032** — the viewer's read-only and no-persistence guarantees — are
  preserved, the first narrowed to the viewer's own sources and the second extended to cover the
  credential.
- **`tests/contract/test_no_review_platform.py` gates this work** and is not modified by it.

## Out of Scope

Each of these can be added later without changing anything this milestone decides:

- **A surface for recording corrections.** The route exists; whether the console or a later milestone
  gives it a face is left open by ADR-0019 §6, and that milestone must say which side of the §1 line
  it lands on.
- **Anything the five nouns name**: reviewer assignment, review queues, workload, review states, and
  dispositions. Not deferred for lack of time — deferred by Principle IX.
- **A cross-tenant view for administrative principals.** It would be a second new read, and one is
  the claim this milestone is measured on.
- **Opening a stored run in the viewer.** The most-wanted thing on this list, and the cost is exact:
  the viewer would need a stored document's **bytes**, and no route serves them —
  `GET /v1/documents/{blob_id}` returns metadata. A milestone that wants this adds that read, widens
  the viewer's two-path allow-list, and restates SC-003. None of those is hard; doing them here would
  cost this milestone the measurement it is defined by.
- **A worker-backlog view.** An operations console has a legitimate reason to want one; if it is
  built, it is named outside the fifteen forbidden words — `backlog`, not `queue` (ADR-0019 §3).
- **A limits or quota view.** Limits are configuration, and showing how close a tenant is to one
  would be a new server-side read that "the page would look better with a gauge" does not justify.
- **Editing retention, limits, routing policies, or callback destinations.** Configuration is the
  deployment's, changed where the deployment's configuration lives.
- **Triggering a retention sweep from the browser.** It runs on the worker's maintenance tick and on
  the command line; a third trigger is a third thing to reason about during an incident.
- **Re-delivery, delivery cancellation, and destination editing.** Delivery is at-least-once and
  comes to rest by itself (ADR-0018); a manual re-send is a second delivery path with its own
  ordering questions.
- **Submitting a document for processing from the console.** The viewer already does that, and it is
  the viewer's.
- **A cookie session, a login route, or single sign-on.** ADR-0019 §4. A deployment wanting SSO puts
  a proxy in front.
- **Per-operator preferences, saved filters, and layout state.** All of them are per-user state, and
  per-user state is the first thing a console grows on its way to becoming the deferred platform.
- **Metrics, charts, and dashboards as a docdoc-owned surface.** `specs/010` deferred them and the
  reason is unchanged: export binds to an endpoint the operator runs.
- **Internationalisation of the console.** English only, matching the viewer.
