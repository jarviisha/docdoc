# Feature Specification: Read-Only Grounding Viewer

**Feature Branch**: `008-grounding-viewer`

**Created**: 2026-08-25

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

**Input**: User description: "giờ tôi muốn dựng UI cho người dùng thì sao nhỉ?" — followed, once the
key decisions were put to them, by "tôi muốn triển khai UI ở mức đủ cho mvp" and a stated preference
for Astryx as the component library. A browser surface that shows a human which page and which
rectangle each extracted value came from, and says plainly when there is no answer to give.

## Why there is a Milestone 8, when Milestone 7 said it was the last

Spec 007 opens by calling itself "the last milestone", and on its own terms it was right: it produced
the Definition of Done stated at the project's founding. Nothing here retracts that. The MVP is
complete and this milestone does not extend it — no new guarantee, no new stage, no new provider, and
no change to any value docdoc produces.

What it changes is **who can see the guarantee**. Every promise the seven milestones built is
currently reachable through exactly two doors: a Python import and a terminal. Principle II makes
source grounding a first-class *feature* rather than an implementation detail, and a feature nobody
outside the project can perceive is one the project is asserting rather than demonstrating. A person
deciding whether to trust an extraction engine is asking a visual question — *show me where this
number came from* — and today the honest answer is "run this command and read the JSON".

The number 8 was previously discussed and declined. Spec 007's clarifications record that the
founding document's "Milestone 8 — Packaging" was deliberately omitted, because everything it listed
already existed. That reasoning does not transfer: this milestone is not packaging, and the thing it
adds does not exist in any form.

## Why this needs no constitutional amendment, stated rather than left to be inferred

The constitution's MVP Scope Constraints list **"a full review UI"** under *Deferred technology* —
postponed, not rejected, and forbidden in the MVP without an approved amendment. Principle IX adds
that supporting corrections "MUST NOT turn the MVP into a workflow or review platform".

This milestone builds neither, and the distinction is not a wording trick:

- **It reviews nothing.** There is no queue of documents awaiting attention, no assignment of a
  document to a person, no approval state, no notion of a document being "done", and no record that
  anyone looked at anything.
- **It corrects nothing.** Principle IX's `Correction` model already exists in
  `src/docdoc/evaluation/corrections.py`, complete with `annotator`, `reason`, and `corrected_value`.
  This milestone does not import it, write one, or offer any control that would produce one. The
  viewer is read-only in the strong sense: there is no request it can make that changes any stored
  byte.
- **It holds no per-user state.** No accounts, no sessions, no database, no server-side memory of a
  visit. The deployment model of Milestone 7 — single node, synchronous, no queue, no database — is
  unchanged, and this milestone adds no infrastructure to the sanctioned stack.

A viewer that displays a result the API already returns is the same category of thing as `--json`
output or `docdoc explain`: a rendering of facts the engine produced. The deferred item is the
workflow platform that would be built *around* such a viewer, and none of it is built here. If a
later milestone wants corrections in the browser, that is the amendment, and this spec is deliberately
arranged so that milestone starts from a viewer rather than from nothing.

**Principle XI is the harder gate**, and it should be. MVP Discipline asks what this earns. The answer
is that it is the only surface on which Principle II's guarantee is legible to someone who does not
write Python, and the guarantee is the product. The scope is bounded by the read-only rule above: the
moment this feature acquires an editable field it has left the scope this argument covers.

## Clarifications

### Session 2026-08-25

Five decisions were put to the user before this spec was written. All five are recorded here as
given, and are not open questions.

- Q: Where does the browser get the document image it draws boxes on, given that
  `GET /v1/documents/{blob_id}` returns metadata and, by explicit design, never the bytes? →
  A: **The browser holds the document.** The user picks a file locally, the page image is produced in
  the browser from that file, and the bytes are sent for extraction. The server hands no document
  bytes back, ever, and the existing endpoint's "Never the bytes" behaviour is untouched. Two
  consequences are accepted rather than worked around: a page reload loses the rendered image and the
  user picks the file again, and a result cannot be re-opened later from its identity alone. The
  rejected alternative — rendering pages to images on the server — would have made an AGPL-licensed
  renderer a required path in an Apache-2.0 project, and would have needed the bytes endpoint and
  therefore authentication that does not exist.
- Q: How far does the viewer go? → A: **Read-only.** See the amendment argument above. No corrections,
  no queue, no assignment, no per-user state.
- Q: Where does the code live and how is it shipped? → A: **In `ui/` in this repository**, built to
  static assets and served by the existing HTTP application behind a new optional extra. Same origin,
  so no cross-origin configuration is introduced. The base install is unchanged and stays unchanged:
  it acquires no dependency, no static asset, and no build output from this milestone.
- Q: What must the API grow for a viewer to be usable? → A: **A way to list the schemas the deployment
  has configured.** No endpoint exposes this today, so a browser has nothing to offer a user but a
  text box and a guess at a name.
- Q: `POST /v1/documents/{blob_id}/extract` refuses to run without a configured store. Accept that, or
  add a storeless path? → A: **Add one.** See the section below, which is the substance of the
  decision rather than a note on it.

A sixth question was raised by `/speckit-clarify` after the spec was first written, because seven of
its success criteria described things only a browser can observe and this repository has no browser or
JavaScript test tooling at all — no automation, no simulated DOM, and four CI job groups that are
entirely Python.

- Q: How are the browser-observable success criteria verified, given that SC-001 demands a drawn
  rectangle equal the returned geometry box for box? → A: **They are not, directly.** The geometry
  mapping and the other decisions the viewer makes are extracted into pure functions and tested
  alone; the rendered interface is not exercised by any automated test. No browser automation and no
  simulated DOM enters this repository. The consequence is accepted with its cost stated: the
  criteria below were rewritten to measure the view model rather than the screen, and the Assumptions
  section names what can now break without any test failing.
- Q: A run happens inside the request and can take tens of seconds. What does the user see while it
  does? → A: **That it is running, and how long it has been running — nothing more.** No progress
  proportion, no percentage, and no estimate, because the architecture cannot know any of them and an
  invented one would be the interface's first lie. No cancel control and no client-side timeout
  either: a run already spending the operator's provider tokens is not stopped by the browser giving
  up on it, so a control promising otherwise would be false. Any bound on the wait belongs to the
  deployment's proxy, and that obligation is now written down rather than discovered when a proxy
  starts killing paid-for runs.
- Q: Which pages does the viewer render, given that the deployment's default page limit is 1000
  (`src/docdoc/ingest/source.py:66`)? → A: **The pages the result names, and the rest only on
  request.** Up-front work is bounded by the number of pages carrying located values rather than by
  the length of the document, so a document at the configured default stays usable. Pages with
  nothing to overlay serve no requirement in this spec, and every rendering mechanism avoided is
  machinery the Q1 decision would have left untested. The viewer must say that it is showing pages
  selectively, so that a partial view is never mistaken for the whole document.
- Q: What does the viewer commit to for a reader who cannot see the overlay? → A: **The list is
  authoritative and the overlay is an aid.** Every fact a rectangle conveys — the field, the value,
  the verdict, the status, the pages — is in the list, which is operable without a pointing device;
  losing the overlay loses the picture and no fact. Status, verdict, and the three geometry states
  never depend on colour alone. **No conformance level is claimed**, because nothing in this
  repository would verify one, and asserting a standard that no test enforces is the same error the
  first clarification made this spec stop making.
- Q: Who is assumed to be able to reach the viewer, given that the interface has no authentication and
  this milestone adds none? → A: **A trusted network, stated as an assumption rather than enforced.**
  No authentication, no sessions, no credential storage. The documentation records the consequence
  that matters and is not obvious: anyone who can load the URL can submit documents and **spend the
  deployment's provider budget**. It also records that the storeless path of FR-001 widens the
  exposure — a deployment with no store previously refused every extraction request, and an operator
  who treated that refusal as a closed door needs telling that it has opened.

## The store coupling, and the sentence it makes false

`POST /v1/documents/{blob_id}/extract` requires a configured store (`src/docdoc/api/app.py:218`). The
requirement is not incidental to that endpoint: it takes a `blob_id`, a `blob_id` exists only after a
submission, and submission is refused outright without a store. **So there is no way, over HTTP, to
run an extraction without the document first coming to rest on disk.**

This is worth stating as a defect rather than a design, for two reasons.

First, the project already rejected an integration for precisely this. The `gcv` adapter declines
Vision's asynchronous API — and therefore declines PDF support on that parser — because it "requires
Cloud Storage buckets for input and output, which is a storage dependency and a place for document
content to come to rest outside the process". The same objection applies with more force to docdoc's
own interface, where the resting place is not a consequence of a third party's API but of ours.

Second, the existing contract already claims otherwise. `specs/007-pipeline-api-cli/contracts/http-api.md`
section 1 reads: *"Running an extraction and reading what it produced do not [need a store], because
the run's response carries the result."* That sentence is false against the code and cannot be made
true by any endpoint whose input is a `blob_id`. There are two ways to end the disagreement — weaken
the contract to match the code, or add the path the contract already describes. This milestone takes
the second, because the sentence is describing the better system: the library can already run a
pipeline over bytes with no store at all (Milestone 7's FR-017), and it is only the HTTP surface that
cannot.

A storeless run produces no terminal artifact and therefore **no job identity to retrieve later**.
That is a property of the choice, not a defect in it, and the contract must say so: a caller who wants
a retrievable identity submits the document first, exactly as today.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See where a value came from (Priority: P1)

Someone evaluating docdoc opens a page in a browser, picks an invoice from their own machine, chooses
a schema from a list, and gets back every field with its value and its verdict — and, for each value
docdoc located, the page it is on with a rectangle drawn around it. Clicking a field lights up its
rectangle; clicking a rectangle reveals which field it belongs to. They wrote no code and read no
documentation to get there.

**Why this priority**: This is the whole milestone. Principle II's guarantee — that a value's origin
is a first-class fact — has been true since Milestone 4 and visible to nobody but a programmer. If
only this story ships, the feature has delivered its entire reason for existing.

**Independent Test**: Feed the committed fixture's run result to the view model and confirm that every
located value produces exactly the boxes the pipeline returned for it, on the pages it named —
compared against the run's own output, box for box. The rendered page is then confirmed once by a
person opening it, because under the sixth clarification of 2026-08-25 nothing automated confirms it.
Delivers the product's central claim to anyone with a browser.

**Acceptance Scenarios**:

1. **Given** a deployment serving the interface with an offline adapter and **no store configured**,
   **When** the user picks a document, chooses a schema, and runs an extraction, **Then** the result
   appears, nothing is written to any store, and no job identity is offered.
2. **Given** a completed run, **When** the user views a page, **Then** every located value on that
   page is drawn at the geometry the pipeline returned, and every rectangle drawn corresponds to a
   value in the result.
3. **Given** a completed run, **When** the user selects a field, **Then** that field's rectangles are
   distinguished from the others and the page carrying the first of them is brought into view.
4. **Given** a completed run, **When** the user selects a drawn rectangle, **Then** the field it
   belongs to is identified.
5. **Given** a value the grounder could not place, **When** the user views the result, **Then** that
   value is listed with its status and **no rectangle is drawn anywhere for it**.

---

### User Story 2 - Be told plainly what docdoc does not know (Priority: P2)

The same person looks at a document where several fields could not be located. Instead of a clean
list of five confident answers and a silent omission of the rest, they see every value the model
asserted, each marked with whether docdoc could place it, and — where it could not — the fact stated
rather than the row removed.

**Why this priority**: "Never a guess" is the claim that distinguishes this project, and a viewer
that quietly drops what it cannot draw would break the claim more effectively than any bug. It is P2
only because Story 1 must exist for there to be anything to be honest about.

**Independent Test**: Apply a fixture producing a mix of located and ungrounded values to the view
model and confirm the count of values it lists equals the count in the result, each carrying its
status, with absent fields distinguished from ungrounded ones. Delivers the honesty guarantee
independently of any drawing — and, unlike Story 1, loses nothing to the untested rendering layer,
because what it asserts is a list rather than a position.

**Acceptance Scenarios**:

1. **Given** a result containing both located and ungrounded values, **When** it is displayed, **Then**
   the number of values shown equals the number in the result and each carries its grounding status.
2. **Given** a value whose parser supplied no geometry at all, **When** it is displayed, **Then** it
   is distinguished from a value whose range covers no tokens, and neither is shown as "not found".
3. **Given** values grounded by different tiers, **When** their scores are displayed, **Then** an
   exact score and a fuzzy score are not placed on a shared scale, ranked against each other, or
   combined into a single number.
4. **Given** a field the model reported as absent, **When** the result is displayed, **Then** it is
   distinguishable from a field asserted with a value that could not be grounded.
5. **Given** a run that failed partway, **When** the result is displayed, **Then** the failing stage is
   named and the results of the stages that completed are shown rather than discarded.

---

### User Story 3 - Install it without acquiring it (Priority: P3)

Someone who wants docdoc as a library installs it and gets exactly what they got before this
milestone: two runtime dependencies, no web assets, no JavaScript, no HTTP framework. Someone who
wants the viewer asks for it by name.

**Why this priority**: The base install's cleanliness is a constitutional requirement and an existing,
tested property. It is P3 not because it matters least but because it is a constraint on the other
two stories rather than a journey of its own — and it is the constraint most easily broken by
accident, since shipping a browser interface from a Python package is exactly how a package acquires
megabytes of assets nobody asked for.

**Independent Test**: In an environment with only the base install, confirm the package imports, the
offline suite passes, and no build output or web asset is present anywhere in the installed
distribution.

**Acceptance Scenarios**:

1. **Given** a base install, **When** the package is imported and the offline suite is run, **Then**
   both succeed with no web framework and no build output installed.
2. **Given** a base install, **When** the installed distribution is inspected, **Then** it contains
   zero files belonging to the viewer.
3. **Given** the viewer extra is installed but the interface has never been built, **When** the
   application starts, **Then** it says so and names what to run, rather than serving a blank page or
   failing obscurely.

### Edge Cases

- **A value spans more than one rectangle.** A located value carries a *tuple* of page-anchored boxes,
  not one box — a value wrapping across two lines has two. Drawing only the first is a wrong answer
  that looks like a right one.
- **A value spans more than one page.** The outcome carries a tuple of page indices. Selecting such a
  field cannot bring "the page" into view because there is more than one.
- **Geometry is unavailable versus geometry is empty.** These are different facts and the result model
  keeps them apart deliberately: no geometry at all means the parser supplied none, an empty tuple
  means geometry exists and this range covers no tokens. A viewer that renders both as "no box" has
  destroyed a distinction the engine spent code to preserve.
- **The page is rotated.** Pages carry a rotation of 0, 90, 180, or 270. If a renderer applies the
  rotation to the image and the coordinates are relative to the unrotated page, every box on that page
  is wrong — and wrong in a way that looks plausible.
- **The document is an image, not a PDF.** The `gcv` parser accepts JPEG and PNG and no PDF at all;
  such a document has exactly one page and needs no page-splitting.
- **The document is larger than the deployment's limit.** The refusal must arrive as the deployment's
  typed error, not as a browser failure or a silent truncation.
- **The extraction fails partway.** The response carries the completed stages' results; a viewer that
  shows only "error" throws away what Milestone 7's FR-066 exists to preserve.
- **No schema is configured.** The list is empty, and the reason belongs on screen: the setting that
  populates it, named — the same failure Milestone 7 fixed for the command line.
- **The user picks a second document without reloading.** The previous result must not survive
  alongside the new document; a stale box drawn over a fresh page is the worst failure this feature
  has, because it is invisible.
- **A result arrives after the user has moved on.** A run in flight for one document completing after
  another has been selected is the same invisible failure arriving by a different route, and closing
  the first door does not close this one.
- **The document is long and mostly empty.** At the deployment's default limit a document may run to
  a thousand pages while the result names four. The four are the view; the rest is a document the
  reader may still want to reach, and neither fact may be hidden by the other.
- **The wait outlives the connection.** A proxy or a browser abandoning a request does not abandon the
  run: the extraction continues, the provider is still paid, and only the answer is lost. An interface
  that reports this as a failed run is describing the connection, not the work.

## Requirements *(mandatory)*

### Functional Requirements

#### The storeless extraction path

- **FR-001**: The HTTP interface MUST provide a way to run an extraction from submitted bytes and a
  schema in one request, returning the full result, **without requiring a configured store**.
- **FR-002**: A storeless run MUST write nothing: no blob, no artifact, and no temporary file that
  outlives the request.
- **FR-003**: A storeless run MUST return no job identity, and the contract MUST state that a caller
  wanting a retrievable identity submits the document first.
- **FR-004**: A storeless run's result MUST agree with the same run performed in-process, and with the
  same run performed through the store-backed path, on every value, verdict, location, and identity
  that is not definitionally about storage.
- **FR-005**: The storeless path MUST enforce the same request-size cap, document-size limit, and
  media-type allowlist as submission, from the bytes and never from a client-declared type.
- **FR-006**: A storeless run MUST surface failures as the same typed, provider-neutral errors, with
  the same statuses, and MUST carry the completed stages' outcomes and results when a run fails
  partway.
- **FR-007**: `specs/007-pipeline-api-cli/contracts/http-api.md` MUST be corrected so its account of
  which endpoints need a store matches the code, including the sentence this milestone makes true and
  the reason a storeless run has no job.
- **FR-008**: If a store *is* configured, the storeless path MUST still write nothing. Whether a run
  persists is a property of the endpoint the caller chose, not of the deployment's configuration.

#### Listing schemas

- **FR-009**: The HTTP interface MUST provide a way to list the schemas the deployment has configured.
- **FR-010**: The listing MUST identify each schema by the name an extraction request accepts, so that
  a listed schema can be run without further translation.
- **FR-011**: The listing MUST NOT expose filesystem paths or any other detail of where a schema came
  from.
- **FR-012**: With no schemas configured, the listing MUST succeed and be empty, and MUST NOT be an
  error — the deployment is validly configured, it just has nothing to offer.

#### What the viewer shows

- **FR-013**: The viewer MUST let a user choose a document from their own machine, choose a schema
  from the deployment's list, and run an extraction.
- **FR-014**: The viewer MUST render the document a page at a time and draw, over each rendered page,
  the rectangles the run returned for values located on that page. Which pages are rendered, and when,
  is fixed by FR-051 through FR-054.
- **FR-015**: The viewer MUST draw **every** rectangle a located value carries, not the first.
- **FR-016**: The viewer MUST display every value in the result, with its value and its validation
  verdict, and MUST NOT omit a value because it could not be drawn.
- **FR-017**: The viewer MUST display each value's grounding status, and MUST show ungrounded values
  as prominently as located ones.
- **FR-018**: The viewer MUST distinguish "the parser supplied no geometry" from "this range covers no
  tokens", and MUST NOT render either as a value that was not found.
- **FR-019**: The viewer MUST distinguish a field the model reported absent from a field asserted with
  a value that could not be grounded.
- **FR-020**: The viewer MUST NOT place exact and fuzzy scores on a shared scale, rank values by score
  across tiers, or present any blended confidence number. Where a score is shown, the tier it belongs
  to MUST be shown with it.
- **FR-021**: Selecting a field MUST distinguish its rectangles from all others; selecting a rectangle
  MUST identify the field it belongs to.
- **FR-022**: Selecting a field that spans multiple pages MUST make that fact visible rather than
  silently choosing one page.
- **FR-023**: The viewer MUST apply no coordinate transformation to returned geometry beyond scaling
  to the rendered page size. Coordinates arrive normalized with a top-left origin, which is already
  the coordinate system of the rendering surface.
- **FR-024**: Where a page declares a rotation, the drawn rectangles MUST land on the same content
  they describe, whatever orientation the page is displayed in.
- **FR-025**: When a run fails partway, the viewer MUST name the failing stage and show the completed
  stages' results.
- **FR-026**: When a deployment has no schemas configured, the viewer MUST say so and name the setting
  that populates the list.
- **FR-027**: When a document is refused for size or type, the viewer MUST show the deployment's typed
  reason.
- **FR-028**: Choosing a new document MUST discard the previous result before the new document is
  displayed. No rectangle from one run may ever be drawn over another run's page.

#### What the viewer must not do

- **FR-029**: The viewer MUST NOT offer any control that edits a value, a verdict, a location, or a
  schema.
- **FR-030**: The viewer MUST NOT issue any request that writes to a store, and MUST NOT construct,
  submit, or persist a correction.
- **FR-031**: The viewer MUST NOT hold per-user state on the server: no account, no session, and no
  server-side record that a document was viewed.
- **FR-032**: The document's bytes MUST NOT be sent anywhere except to the deployment's own extraction
  endpoint, and MUST NOT be written to browser-persistent storage.
- **FR-033**: Document content, extracted values, and credentials MUST NOT appear in any log the
  server emits for these requests, consistent with the constitution's security constraints and
  Milestone 7's FR-043.

#### Packaging and serving

- **FR-034**: The viewer MUST be served by the existing HTTP application from the same origin, so that
  no cross-origin configuration is introduced.
- **FR-035**: The viewer MUST be gated behind an optional extra, and the base install MUST acquire
  nothing from this milestone — no dependency, no static asset, no build output.
- **FR-036**: The existing base-install guards MUST be extended to cover the new extra, so that a
  regression is a test failure rather than a discovery.
- **FR-037**: With the extra installed but the interface never built, the application MUST report that
  fact and name the step that fixes it.
- **FR-038**: The build MUST be reproducible from a clean checkout with a documented command, and
  build output MUST NOT be committed to the repository.
- **FR-039**: The repository's licence position MUST remain unchanged: no dependency introduced by
  this milestone may impose obligations incompatible with Apache-2.0 on docdoc itself, and the
  dependency set MUST be recorded with its licences.
- **FR-040**: The roadmap in `README.md` MUST gain this milestone and its status, and this spec's
  Status field MUST move to `Implemented` in the same change.

#### The verification boundary

- **FR-041**: Every decision the viewer makes about what to show MUST be computed by functions that
  are independent of rendering: which boxes belong to a value and where they sit, which values are
  listed and in what state, what a failure says, and what a new document clears. These are the
  **view model**, and under the 2026-08-25 clarification they are the only part of this milestone
  under automated test.
- **FR-042**: The view model MUST be testable without a browser, a simulated DOM, or a rendering
  surface of any kind, taking a run's result as input and returning what should be displayed.
- **FR-043**: The rendering layer MUST contain no decision the view model could have made. Any
  conditional in a component that changes which value, box, state, or message a user sees is a
  requirement that has escaped the tested surface, and moving it back is not optional. This is the
  requirement that decides whether the clarification's accepted gap stays small or swallows the
  feature.
- **FR-044**: This spec, the plan, and the repository's user-facing documentation MUST state that the
  rendered interface carries no automated test, rather than leaving a reader to infer coverage from
  the presence of success criteria about what a user sees.

#### While a run is in flight

These belong with "What the viewer shows" and are numbered here to keep identifiers stable; they were
added by the second clarification of 2026-08-25.

- **FR-045**: While a run is in flight the viewer MUST show that it is running and MUST show how long
  it has been running.
- **FR-046**: The viewer MUST NOT display a progress proportion, a percentage, a stage count, or an
  estimated time remaining. The interface has no basis for any of them, and a fabricated one is the
  same category of error as a fabricated location.
- **FR-047**: The viewer MUST NOT offer a control that claims to cancel a run, because closing the
  browser stops the waiting and not the work. If the viewer offers a way to stop *waiting*, it MUST
  say that the run continues and its cost is already incurred.
- **FR-048**: The viewer MUST NOT impose a client-side timeout on an extraction. Any bound on the wait
  belongs to the deployment.
- **FR-049**: While a run is in flight, starting a second run MUST be unavailable, and a result that
  arrives for a document that is no longer the selected one MUST be discarded rather than displayed.
- **FR-050**: The documentation MUST state that a run continues after the page is closed, and that a
  deployment placing a proxy in front of the interface must allow a request duration at least as long
  as its slowest expected extraction — otherwise the proxy terminates runs the deployment has already
  paid for, and the failure looks like a viewer bug.

#### Which pages are rendered

Added by the third clarification of 2026-08-25, and numbered here for the same reason as the block
above.

- **FR-051**: The viewer MUST render the pages the result names for located values, and MUST NOT
  render every page of a document up front.
- **FR-052**: Pages carrying no located value MUST stay reachable and MUST be rendered when the user
  navigates to one.
- **FR-053**: The work performed before any navigation MUST be bounded by the number of pages the
  result names, not by the length of the document. A document at the deployment's configured page
  limit MUST remain usable.
- **FR-054**: The viewer MUST make plain that it is showing pages selectively. A reader who takes
  "the pages with values on them" for "the whole document" has been misled about what they are
  looking at, and by a viewer whose entire purpose is telling them what is really there.

#### Reaching the facts without the picture

Added by the fourth clarification of 2026-08-25.

- **FR-055**: The list of values MUST carry every fact the overlay conveys — the field, its value, its
  verdict, its grounding status, and the pages it was located on — so that no fact in this interface
  is reachable only by looking at a rectangle.
- **FR-056**: The list MUST be navigable and operable without a pointing device.
- **FR-057**: Grounding status, validation verdict, and the three geometry states MUST NOT be conveyed
  by colour alone. Each MUST carry a textual equivalent. A three-way distinction encoded as three
  colours is FR-018's collapse performed on one reader instead of all of them.
- **FR-058**: The overlay MUST be a visual aid. Removing it MUST NOT remove any fact from the
  interface.
- **FR-059**: This milestone MUST NOT claim a conformance level. The documentation states the
  guarantee it keeps — every fact is in the list, and the list works without a pointer — and asserts
  no standard, because no test here enforces one.

#### Who can reach it

Added by the fifth clarification of 2026-08-25.

- **FR-060**: This milestone MUST NOT add authentication, sessions, or credential storage. The
  constitution's MVP security list does not require authentication, and adding it here would pull
  credential handling into a milestone whose scope argument rests on adding no infrastructure.
- **FR-061**: The documentation MUST record that the interface is unauthenticated **and** name the
  consequence: anyone who can reach it can submit documents and spend the deployment's provider
  budget. The second half is the part an operator cannot infer from the first.
- **FR-062**: The documentation MUST state that the interface is intended for a trusted network and
  is not to be exposed to an untrusted one.
- **FR-063**: The documentation MUST record that the storeless path changes the exposure of a
  deployment configured without a store. Such a deployment previously refused every extraction
  request over HTTP; after this milestone it serves them. An operator who read that refusal as a
  closed door is entitled to learn it has opened, from us rather than from a bill.

### Key Entities

- **Located value**: one extracted value together with its grounding status, its optional source
  range, the pages it touches, and the ordered boxes covering it. Its geometry has three states that
  are not interchangeable: absent because the parser supplied none, present but empty, and present
  with boxes.
- **Grounding status**: one of exactly three — exact, fuzzy, ungrounded. A closed vocabulary that
  ADR-0005 makes constitutional; the viewer displays it and never extends it.
- **Score**: a docdoc-computed number that is not comparable across tiers. It travels with its tier or
  it does not travel.
- **Schema listing entry**: the name by which a deployment's configured schema can be requested, and
  nothing about where it lives.
- **Run outcome**: what one extraction produced — the values, the verdicts, the per-stage outcomes,
  and, if the run failed, the stage it failed at and the results that survived it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

**What "verified" means in this milestone, and what it does not.** Under the sixth clarification of
2026-08-25, the rendered interface carries no automated test. Every criterion below that concerns what
a user sees is measured against the **view model** — the pure functions of FR-041 that turn a run's
result into what should be drawn and listed — and not against a rendered page. Each such criterion
says so on its own line rather than relying on this paragraph, because a criterion that says
"displayed" and is checked against a function measures something other than what it claims, and this
repository has already had to amend two criteria that measured nothing. The gap this leaves is named
in Assumptions, not buried here.

- **SC-001**: For every located value in a run, the view model emits **exactly one box entry per box
  the run returned**, each carrying the same page index and the same four coordinates, in 100% of
  cases — compared against the run's own output, box for box. A test asserting that *some* entry was
  emitted does not satisfy this criterion; emitting one entry for a value carrying three fails it.
  - **Measured on the view model, not the screen.** That the browser draws these entries where the
    view model places them is not verified by anything.
- **SC-002**: The view model emits box entries for **0% of ungrounded values**, and includes 100% of
  them in what it lists. The two halves are counted together for the same reason Milestone 7's SC-001
  counts both of its halves: a viewer that emits nothing satisfies the first alone, and one that emits
  everywhere satisfies the second alone.
  - Measured on the view model, not the screen.
- **SC-003**: The number of extracted values the view model lists equals the number the run produced,
  in 100% of runs, and every schema field the model reported absent is listed as absent rather than
  omitted. Both halves are needed: counting only asserted values lets a viewer drop the absent ones
  silently, and a field missing from the list is indistinguishable from a field docdoc never had a
  schema for. Any difference is a value the viewer decided not to mention.
  - Measured on the view model, not the screen.
- **SC-004**: Over a fixture set containing all three geometry states — unavailable, empty, and
  populated — the view model represents the three as three distinct states in 100% of cases, and 0%
  are collapsed into another.
  - Measured on the view model, not the screen. That the three then *look* different to a user is
    exactly the kind of claim this milestone stopped being able to make.
- **SC-017**: 100% of the facts the overlay conveys — field, value, verdict, grounding status, and
  pages — are present in what the view model lists, and the view model supplies a textual label for
  100% of statuses, verdicts, and geometry states, so no distinction can depend on a colour the model
  never supplied.
  - Measured on the view model, not the screen. That the list is genuinely operable without a
    pointing device is a property of the rendered interface, and under the first clarification a
    person confirms it rather than a test. Numbered out of order because it belongs beside SC-004,
    which is the criterion it generalises.
- **SC-005**: A base install acquires **zero** files, zero dependencies, and zero build artifacts from
  this milestone, and the full offline suite passes without the viewer extra installed.
- **SC-006**: A result obtained through the storeless path and the same result obtained through the
  store-backed path agree on 100% of values, verdicts, and locations.
- **SC-007**: A storeless run over a deployment with no store configured completes successfully and
  leaves **zero** bytes written: no blob, no artifact, no temporary file.
- **SC-008**: A storeless run over a deployment **with** a store configured also leaves zero artifacts
  and zero blobs, proving the endpoint and not the configuration decides.
- **SC-009**: Across a run over a document containing known distinctive strings, 0% of those strings
  and 0% of extracted values appear in the logs the server emits for the viewer's requests.
- **SC-010**: 100% of the failure paths a user can reach — no schemas configured, document too large,
  unsupported type, provider failure, mid-run failure — cause the view model to carry an explanation
  naming the cause; 0% leave it carrying an empty result or an untyped error.
  - Measured on the view model, not the screen.
- **SC-011**: Applying a second document to the view model clears the first result before any box
  entry from the new run exists, in 100% of cases, verified by a test that fails if a prior run's box
  entries survive the transition.
  - Measured on the view model, not the screen. This one loses the most in translation: the failure it
    guards against — a stale rectangle drawn over a fresh page — is invisible precisely because it
    renders without error, and rendering is now the untested half.
- **SC-012**: At merge, `specs/007-pipeline-api-cli/contracts/http-api.md` contains no claim about
  store requirements that the code contradicts, verified by a test asserting the storeless path
  behaves as the corrected contract describes.
- **SC-013**: The module through which the viewer makes every request can construct zero requests that
  write to a store and zero corrections, verified by exercising that module across the full set of
  user actions rather than by inspecting the source. This is measurable without a browser only
  because FR-041 puts request construction in the view model; a component that called the network
  directly would put this criterion out of reach along with the rest.
- **SC-014**: At merge, 100% of the dependencies this milestone introduces are recorded with their
  licences, zero of them impose an obligation incompatible with Apache-2.0 on docdoc itself, and the
  interface builds from a clean checkout with the documented command while zero build outputs are
  committed to the repository. This is the criterion for FR-038 and FR-039, and it is stated because
  a licence obligation acquired by accident is discovered at the worst possible moment.
- **SC-018**: At merge, the documentation states **all seven** of the facts this milestone's
  requirements oblige it to state, and zero are absent: that the rendered interface carries no
  automated test (FR-044); that a run continues after the page is closed and what that demands of a
  proxy (FR-050); that pages are shown selectively (FR-054); that no accessibility conformance level
  is claimed (FR-059); that the interface is unauthenticated and every visitor spends the provider
  budget (FR-061); that it belongs on a trusted network (FR-062); and that a store-less deployment
  now serves extractions it used to refuse (FR-063). These are counted together in one criterion
  because they share a failure mode: each is a sentence that costs nothing to omit and is discovered
  missing only by the person it would have warned.
- **SC-015**: While a run is in flight the view model reports it as running and exposes the elapsed
  time, and exposes **zero** progress proportions, percentages, stage counts, or completion estimates.
  A result arriving for a document that is no longer the selected one is discarded in 100% of cases.
  - Measured on the view model, not the screen. The second half is counted because it is the same
    invisible failure as SC-011 reached by timing rather than by selection, and a test that only
    covers the selection route would leave the door open.
- **SC-016**: For a result naming *N* pages, the view model asks for exactly those *N* pages to be
  rendered before any navigation, and zero others — including for a document at the deployment's
  configured page limit, where the count of pages asked for stays *N* rather than rising with the
  document. Every page the document has remains reachable, so 0% of the document is unreachable.
  - Measured on the view model, not the screen.

## Assumptions

- **The rendered interface is not under test, and this is what that costs.** The sixth clarification
  of 2026-08-25 chose to extract the viewer's decisions into pure functions and test those alone. What
  no test will catch, stated plainly rather than left as a shape in the coverage report: a box entry
  the view model computed correctly and the renderer draws at the wrong place or the wrong scale; a
  rotated page whose boxes land on the wrong content; a value listed in the model and hidden by a
  layout or a style; the three geometry states rendered so similarly that the distinction FR-018
  preserves is invisible to a reader; and a stale rectangle surviving a document change in the
  rendering layer after the model cleared it. The mitigation is FR-043 — a renderer that decides
  nothing has little room to be wrong on its own — and it is a mitigation, not a substitute. If this
  gap is later judged too large, the change is additive: the view model is already the seam a browser
  test would attach to, so adding one does not require rewriting the interface.
- **The user's browser can render the documents they pick.** Digital PDFs and the image types the
  parsers accept are in scope; a format no parser accepts is refused by the existing limits before
  rendering is reached.
- **A page reload loses the view.** The browser holds the document and the server hands no bytes back,
  so a result cannot be reopened later from its identity. This is the accepted consequence of the
  first decision, not an oversight, and no persistence is added to soften it.
- **The deployment configures an adapter and, for the offline demonstration, an offline one.** This
  milestone adds no provider, no adapter, and no credential handling.
- **Astryx is in beta.** The chosen component library is pre-1.0 and may make breaking changes inside
  this milestone's lifetime. The mitigation is that no docdoc guarantee depends on it: it supplies
  presentation only, every requirement above is stated in terms of what a user can see rather than of
  any component, and the fallback if a breaking change lands mid-milestone is to pin the last working
  version and proceed, since none of FR-013 through FR-028 needs a feature specific to it. The
  dependency is recorded, with its licence, under FR-039.
- **The network the interface is served on is trusted.** There is no authentication, and this
  milestone adds none. The assumption is load-bearing in a way a reader should not have to work out:
  the cost of a stranger reaching this interface is not only that they see documents, but that they
  spend the deployment's provider budget one extraction at a time. docdoc ships no `serve` command, so
  it cannot choose a safer default on the operator's behalf — the bind address belongs to whoever
  starts the server, which is precisely why the assumption is written down instead of assumed.
- **The single-node, synchronous deployment model of Milestone 7 is unchanged.** No queue, no
  database, no object store, and no multi-user assumptions are introduced.
- **`docdoc explain` stays on the command line.** Exposing provenance over HTTP was considered and
  declined for this milestone to keep the added API surface to the two endpoints the viewer cannot
  work without. A viewer that wants to show the artifact chain is a later change, and the decision to
  defer it is recorded here so its absence reads as a choice.
