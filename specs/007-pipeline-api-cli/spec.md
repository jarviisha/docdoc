# Feature Specification: Pipeline, Artifact Store, CLI, and HTTP API

**Feature Branch**: `007-pipeline-api-cli`

**Created**: 2026-08-22

**Status**: Draft

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

**Input**: User description: "dựng spec và tách nhánh cho milestone cuối này nhé" — specify the
project's final milestone, Milestone 7, listed in the roadmap as *API and CLI*.

## Why this milestone is not what its title says

The roadmap calls Milestone 7 "API and CLI". Both of those are *entry points*. The body of the work
is two things that do not exist yet and that five earlier milestones deferred here by name:

1. **A pipeline that is a thing, not a sequence someone wrote out again.** Parse → extract → ground
   → validate is currently assembled inside one private function of the recording layer
   (`src/docdoc/recording/record.py`), which exists to serve evaluation. Principle XI requires the
   stages to be explicit, identified, and versioned so an executor can change without touching a
   domain type; nothing in the codebase holds that shape today.
2. **An artifact store.** ADR-0003's central promise — changing a prompt invalidates the extraction
   and *reuses the parse* — has never been keepable, because nothing stores an artifact. The
   recording layer documents this as a known limitation in its own module docstring, and every
   evaluation run re-parses every document because of it.

Everything the earlier milestones deferred "to the pipeline milestone" lands here: caching
(002, 003), persistence of artifacts (004, 005), counters, latency histograms and tracing (002,
003), the match-view cache ADR-0006 specifies and grounding does not have, and the last two typed
errors the constitution's error model names and no code defines — `PipelineError` and
`ArtifactError`.

This is the last milestone. What it must produce is the Definition of Done stated at the founding of
the project: a PDF goes in one end of a command, and a human can ask any extracted value which page
and which rectangle it came from.

## Clarifications

### Session 2026-08-22

- Q: Does this milestone ship a container image and a release process, or was the roadmap's omission
  of the founding document's "Milestone 8 — Packaging" deliberate? → A: **Deliberate.** Everything
  else that milestone listed — PyPI packaging, documentation, examples, tests, CI — already exists.
  A container image and a release process are out of scope, here and for the MVP. The constitution's
  development-compose line therefore needs no reconciling: nothing in this feature requires a
  database or an object store, and nothing in it ships an image.
- Q: Is `TODO(PRE_1_0_VERSIONING)` — the constitution's last open decision — resolved by this
  milestone, or does it outlive it? → A: **Resolved here**, by its own ADR, recorded in the
  constitution's decision table in the same change (FR-057). This is the final milestone; a decision
  that outlives the last plan capable of carrying it stays open forever. The policy is a governance
  deliverable and changes no runtime behaviour.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get an answer out of a PDF with one command (Priority: P1)

Someone who has just installed docdoc has an invoice and a schema. They run one command and get
back every field, its value, its verdict, and the page and rectangle each value was read from. They
did not deploy a service, start a database, write a script, or read the library's API documentation
to do it.

**Why this priority**: This *is* the project's Definition of Done. Every guarantee the six previous
milestones built — a located value, a checked value, a measured value — is unreachable today except
through Python. A developer evaluating an IDP engine will not write a script to decide whether to
keep reading.

**Independent Test**: Run the command against a committed fixture PDF and a committed schema with no
credentials and no network, and confirm the output names each field's value, its verdict, its page,
and its bounding box. Delivers the whole product to anyone with a shell.

**Acceptance Scenarios**:

1. **Given** a digital PDF, a schema on the configured search path, and an offline adapter, **When**
   the user runs the extract command, **Then** the output contains one entry per schema field with
   its value, its grounding status, its page, and its bounding box, and the process exits zero.
2. **Given** the same inputs, **When** the user asks for machine-readable output, **Then** the
   result is a single structured document containing the same facts plus the run's identities, and
   nothing else is written to standard output.
3. **Given** a document whose stated total disagrees with the sum of its lines, **When** the user
   runs the command, **Then** the output reports an invalid verdict naming the field, the expected
   value, the actual value, and the place on the page the actual value was read from, and the exit
   code distinguishes "ran, and the document failed validation" from "could not run".
4. **Given** a document that fails partway, **When** the run ends, **Then** the output names the
   stage that failed and preserves the results of the stages that succeeded, rather than reporting
   nothing.
5. **Given** no schema search path configured, **When** the user runs the command, **Then** the
   error says the registry is empty and names the setting that populates it, rather than reporting
   that the schema does not exist.

---

### User Story 2 - Change the prompt without paying for the parse again (Priority: P2)

Someone iterating on a schema or a prompt runs the same document repeatedly. The second run does not
re-parse it, does not call the cloud parser again, and does not rebuild the match view. When they
change the model instead, the parse is still reused and everything downstream of extraction is
recomputed. When they change nothing, the whole run is a lookup.

**Why this priority**: This is the difference between the artifact chain being a design and being a
mechanism. Re-parsing on every run is a billable, repeated cost on the cloud parser path, and the
project has been quietly paying it since Milestone 2. It is second only to having a command at all
because a correct answer that costs full price every time is still usable; the reverse is not.

**Independent Test**: Run one document twice with an instrumented counter on each stage; assert the
second run performs zero parses. Then change only the prompt and assert the parse count is still
zero while extraction runs. Delivers measurable cost reduction with no change to any result.

**Acceptance Scenarios**:

1. **Given** a document processed once, **When** it is processed again with identical inputs,
   **Then** no stage is executed, every result is byte-identical to the first run, and the run
   reports which stages were reused.
2. **Given** a document processed once, **When** only the prompt or the schema changes, **Then** the
   parse is reused and extraction, grounding, and validation are recomputed.
3. **Given** a document processed once, **When** only a validation rule changes, **Then** the parse
   is reused, and the run recomputes exactly the stages whose recorded inputs moved.
4. **Given** a stored artifact whose recorded identity does not match its content, **When** it is
   read, **Then** the run raises an explicit error rather than returning it.
5. **Given** an artifact stored by an earlier, incompatible version of docdoc, **When** it is read,
   **Then** it is not returned as a reusable result.

---

### User Story 3 - Call it over HTTP (Priority: P3)

A team wants docdoc inside a service rather than inside their Python process. They send a document,
ask for an extraction against a named schema, and fetch the result by its identity. The identity
they get back is the same one the library computes, so a result fetched over HTTP and a result
computed locally are provably the same result.

**Why this priority**: It is the milestone's title and the shape most deployments will use. It is
third because it delivers no capability the command line does not already deliver; it delivers
*reach*. A team can wrap the library themselves; they cannot wrap a library that has no pipeline.

**Independent Test**: Submit a document, request an extraction, poll the job, fetch the result, and
confirm the returned identity equals the one the library computes for the same inputs. Delivers
network access to the whole pipeline with no queue and no database.

**Acceptance Scenarios**:

1. **Given** a running service, **When** a document is submitted, **Then** the response carries the
   document's identity and the same bytes submitted twice yield the same identity.
2. **Given** a submitted document, **When** an extraction is requested against a named schema,
   **Then** the response carries a job identity, and that identity equals the run's terminal
   artifact identity.
3. **Given** a job identity, **When** the result is fetched, **Then** it carries the same values,
   verdicts, locations, and identities the command line produces for the same inputs.
4. **Given** a job identity that was never produced, **When** it is fetched, **Then** the service
   reports it as unknown and does not fabricate a pending state for it.
5. **Given** a document exceeding the configured size limit or carrying a disallowed type, **When**
   it is submitted, **Then** it is refused before any provider is contacted and no temporary file
   survives the request.
6. **Given** a request that fails at any stage, **When** the error is returned, **Then** it is a
   stable, provider-neutral error naming the stage, and it does not quote the document's contents.

---

### User Story 4 - Find out where an identity came from (Priority: P4)

Someone looking at a log line containing an artifact identity wants to know why it is that value:
which inputs went into it, which stage produced it, and what would have to change to move it.

**Why this priority**: ADR-0003 accepted the cost of unreadable cache keys on the explicit condition
that a tool would explain them — "provide a CLI to explain how an id was derived". Without it, the
first cache-correctness incident is unarguable in both directions: nobody can show the reuse was
right, and nobody can show it was wrong. It is fourth because it is a debugging affordance, not a
capability, and it is small once the store exists.

**Independent Test**: Take an identity produced by a run and ask the tool to explain it; confirm the
output names the stage, the input identity, the processor and its version, and each input folded
into the options hash. Delivers auditability of every cache decision.

**Acceptance Scenarios**:

1. **Given** an artifact identity present in the store, **When** the user asks for its derivation,
   **Then** the output names the stage, the input artifact identity, the processor identity and
   version, and every input folded into the options hash, without exposing document content.
2. **Given** an identity present in the store, **When** the user asks for its derivation chain,
   **Then** the output walks back to the source blob identity.
3. **Given** an identity not present in the store, **When** the user asks for its derivation,
   **Then** the tool says so plainly and does not guess.

---

### User Story 5 - See what a run did without seeing what the document said (Priority: P5)

An operator running docdoc on real documents needs to know how long each stage took, which provider
and model answered, how many tokens it cost, and how often things fail — and must be able to hand
those logs to someone who is not cleared to read the documents themselves.

**Why this priority**: The constitution requires both halves of this at MVP: structured logging with
request id, processing id, step id, latency, provider, model and token usage, *and* a prohibition on
logging document contents, PII, API keys, or prompts. Neither half exists. It is last because the
four stories above are each usable without it, and it must not be dropped because the prohibition is
the kind of thing that is cheap now and a disclosure later.

**Independent Test**: Run a document containing known unique strings and assert none of those
strings, and no credential, appears anywhere in the emitted logs, while every required field does.
Delivers operability without creating a second copy of the documents in the log store.

**Acceptance Scenarios**:

1. **Given** a completed run, **When** its log output is examined, **Then** every stage emitted one
   structured event carrying the request identity, the processing identity, the step identity, the
   duration, the outcome, and — where a provider was used — the provider, the model, and the token
   usage.
2. **Given** a document containing a distinctive string and a run using a credential, **When** the
   full log output is searched, **Then** neither the string, nor any extracted value, nor the
   credential, nor the prompt body appears in it.
3. **Given** a run that reused stored artifacts, **When** its log output is examined, **Then** each
   reused stage is recorded as reused, so a cost question can be answered from logs alone.

---

### Edge Cases

- **A processor changes its output without bumping its version.** The store returns a stale artifact
  and the system cannot detect it — ADR-0003 records this as a review obligation. This milestone
  must not pretend to solve it, and must make it survivable: the derivation of every identity is
  inspectable (US4), and the store must be clearable so a suspect result can be reproduced from
  scratch.
- **A stored artifact was written by an earlier docdoc whose result shape has since changed.**
  Reading it into the current shape either fails loudly or, worse, succeeds with missing fields
  defaulted. It must never be returned as a reusable result.
- **Two runs process the same document concurrently and both miss the store.** Both compute the same
  content-addressed identity and both write. Because artifacts are immutable and addressed by their
  content, the outcome must be the same artifact either way — but a partially written file must
  never be readable as a complete one.
- **The store's root is unwritable, full, or read-only.** A run must still be able to produce a
  correct result, or fail with an error that names the store rather than the document.
- **The store is shared between two docdoc versions.** Neither may return the other's artifacts
  unless they are genuinely interchangeable.
- **A document fails at the third stage.** The command line, the HTTP interface, and the recorder
  must agree on what is returned: the stages that succeeded, the stage that failed, and its typed
  error class — never the exception message, which can quote the document.
- **A run with no credentials against a schema whose adapter needs them.** The failure must name the
  missing configuration, not the document.
- **An upload is aborted mid-request, or the process is killed while a temporary file exists.**
  Temporary files must not accumulate.
- **The same bytes are submitted twice to the HTTP interface.** The second submission must not
  duplicate storage or produce a different identity.
- **A result is requested for a job whose inputs are no longer resolvable** — for example, the store
  was cleared. The interface must report the result as unavailable rather than recomputing it
  silently under different inputs.
- **A CLI invocation writes machine-readable output while a warning occurs.** The structured
  document on standard output must stay parseable; diagnostics belong on standard error.
- **An extremely large document.** The size limit is enforced before any parse and before any
  provider call, not after.

## Requirements *(mandatory)*

### Functional Requirements

**The pipeline as an explicit stage machine**

- **FR-001**: The system MUST provide a pipeline that executes the stages parse → extract → ground →
  validate for one document and returns exactly one result, or raises an explicit typed error.
- **FR-002**: Each stage MUST be an explicit, separately identified step carrying a stable processor
  identity and a version that moves whenever its output moves for fixed inputs. The pipeline MUST NOT
  be a generic DAG engine and MUST NOT accept a user-supplied stage graph.
- **FR-003**: The pipeline MUST NOT redefine, reimplement, or re-derive any behaviour of the parse,
  extraction, grounding, or validation stages. It sequences them, records what happened, and decides
  reuse. Any rule about *what a stage means* stays in that stage's layer.
- **FR-004**: A run that fails at a stage MUST return every result the stages before it produced,
  the stage that failed, and the typed error's class, and MUST NOT discard partial results.
- **FR-005**: A failure MUST be attributed to the stage whose layer declared the error, not to the
  stage that was executing when it surfaced.
- **FR-006**: The pipeline MUST carry its own `pipeline_version`, and that version MUST be folded
  into the terminal artifact identity as ADR-0003 requires.
- **FR-007**: The run's `processing_id` MUST be the terminal artifact identity, so it transitively
  covers every result-affecting input. The system MUST NOT introduce a second, separately computed
  run identifier alongside it.
- **FR-008**: The pipeline MUST be usable synchronously, in-process, with no service running, no
  database, and no object store.
- **FR-009**: The existing recording step of Milestone 6 MUST be reimplemented as a caller of this
  pipeline rather than continuing to sequence the stages itself. Two definitions of the stage order
  in one repository is the condition this requirement exists to prevent.
- **FR-010**: Retries MUST be permitted for provider and network calls only. Validation, grounding,
  and schema errors MUST NOT be retried.

**The artifact store**

- **FR-011**: The system MUST provide an artifact store addressed by the content-addressed identity
  of ADR-0003, in which every artifact is immutable and the store is append-only.
- **FR-012**: Before executing a stage, the pipeline MUST compute that stage's artifact identity from
  its recorded inputs and MUST return the stored artifact instead of executing the stage when one is
  present. A reused stage MUST produce a result byte-identical to the one it replaced.
- **FR-013**: Reuse MUST be partial and per-stage. A change confined to one stage's inputs MUST
  invalidate that stage and everything downstream of it, and MUST reuse everything upstream of it.
- **FR-014**: The store MUST refuse to return an artifact whose stored content does not match the
  identity it is stored under, raising an explicit error.
- **FR-015**: The store MUST record, alongside each artifact, the artifact-format version under
  which it was written, and MUST NOT return an artifact written under an incompatible one.
- **FR-016**: A partially written artifact MUST NOT be readable as a complete one.
- **FR-017**: The store MUST be optional. With no store configured, every run recomputes every stage
  and produces identical results; nothing about correctness may depend on the store being present.
- **FR-018**: The store MUST NOT require a database or an object store. It MUST NOT prevent one being
  added later behind the same boundary.
- **FR-019**: The system MUST provide a way to clear the store, or a subset of it, so a suspect
  result can be reproduced from scratch.
- **FR-020**: Grounding's comparison-time match view MUST be cached by document identity and match
  view version, as ADR-0006 specifies and as grounding does not currently do.
- **FR-021**: The system MUST store the submitted source bytes under their blob identity when a
  caller submits a document for later processing, and MUST NOT store two copies of identical bytes.
- **FR-022**: Garbage collection of unreachable artifacts is out of scope, and the store MUST NOT be
  designed in a way that makes adding it a breaking change.

**Explaining an identity**

- **FR-023**: The system MUST be able to explain how any artifact identity it produced was derived:
  the stage, the input artifact identity, the processor identity and version, and every input folded
  into that stage's options hash.
- **FR-024**: The explanation MUST be able to walk the chain back to the source blob identity.
- **FR-025**: The explanation MUST NOT expose document content, extracted values, prompt bodies, or
  credentials. It explains identities, not documents.

**The command line**

- **FR-026**: The system MUST provide a command-line interface offering, at minimum: parse a
  document, extract against a named schema and run the full pipeline, inspect a result's values and
  their locations, explain an identity, and evaluate a golden set.
- **FR-027**: Every command MUST support both a human-readable and a machine-readable output form,
  and the machine-readable form MUST be the only thing written to standard output when selected.
  Diagnostics, warnings, and progress MUST go to standard error.
- **FR-028**: Exit codes MUST distinguish, at minimum: the run succeeded and the document is valid;
  the run succeeded and the document is invalid; the run could not complete. A caller MUST NOT have
  to parse output text to tell these apart.
- **FR-029**: The offline path — a digital PDF, an offline adapter, grounding, validation, and
  evaluation — MUST be runnable from the command line with no credentials and no network.
- **FR-030**: The command line MUST NOT contain extraction, grounding, or validation logic. It
  parses arguments, calls the pipeline, and formats a result.
- **FR-031**: Where configuration is already environment-driven, the command line MUST accept the
  same settings as explicit arguments and MUST NOT introduce a second, differently-named
  configuration vocabulary.

**The HTTP interface**

- **FR-032**: The system MUST expose an HTTP interface offering: submit a document, retrieve a
  document's metadata, request an extraction against a named schema, retrieve a job's status, and
  retrieve a job's result.
- **FR-033**: A job MUST be identified by the run's terminal artifact identity, and the interface
  MUST NOT introduce a queue, a worker pool, a background executor, or a job table.
- **FR-034**: A result retrieved over HTTP MUST carry the same values, verdicts, locations, and
  identities as the same run performed in-process. The interface serialises a result; it does not
  produce a different one.
- **FR-035**: A job identity that was never produced MUST be reported as unknown. The interface MUST
  NOT report it as pending.
- **FR-036**: A result whose stored artifacts are no longer available MUST be reported as
  unavailable, and MUST NOT be silently recomputed under inputs that may since have changed.
- **FR-037**: Every error MUST be returned as a stable, provider-neutral, typed error naming the
  stage at fault, and MUST NOT include document content, extracted values, prompt bodies, or
  provider error text that may quote either.
- **FR-038**: The HTTP interface MUST NOT be required to use the library, and its dependencies MUST
  NOT enter the base install.

**Limits, security, and data handling**

- **FR-039**: The system MUST enforce a configurable maximum document size and a configurable
  request size limit, and MUST refuse an oversized input before parsing it and before contacting any
  provider.
- **FR-040**: The system MUST enforce an allowlist of accepted document types and MUST refuse
  anything outside it with an explicit error.
- **FR-041**: Temporary files MUST be removed when a request completes, fails, or is aborted, and
  MUST NOT accumulate across runs.
- **FR-042**: Provider credentials MUST be read only where a provider adapter needs them, MUST NOT
  appear in any result, artifact, log event, error message, or explanation, and MUST NOT be folded
  into any identity.
- **FR-043**: Document contents, extracted values, personally identifying information, credentials,
  and prompt bodies MUST NOT be written to logs. Logs carry hashes and identifiers.
- **FR-044**: Stored artifacts contain extracted values by nature. The system MUST state where the
  store writes and MUST NOT write it to a shared or world-readable location by default.

**Observability**

- **FR-045**: Every stage execution MUST emit exactly one structured log event carrying the request
  identity, the processing identity, the step identity, the duration, and the outcome.
- **FR-046**: Where a provider answered, the event MUST carry the provider, the model, and the token
  usage; where a stage was reused from the store, the event MUST record it as reused.
- **FR-047**: The system MUST expose counters and latency measurements per stage, including a count
  of stages reused versus executed.
- **FR-048**: Distributed tracing MUST be supported where practical and MUST NOT be required: a run
  with no tracing configured behaves identically.
- **FR-049**: Observability MUST NOT change any result, any identity, or any verdict.

**Errors**

- **FR-050**: The system MUST define the two remaining typed errors named by the constitution's
  error model — a pipeline error and an artifact error — and MUST reuse the existing typed errors of
  the layers below rather than wrapping them in new names.
- **FR-051**: No untyped exception may escape the pipeline, the command line, or the HTTP interface
  to a caller.

**Boundaries and packaging**

- **FR-052**: The domain model MUST NOT depend on HTTP, on the command line, on an ORM, or on a
  database. The layer direction MUST be extended to cover the new layers and MUST be machine-checked
  in CI, as it is for every existing layer.
- **FR-053**: The base install MUST NOT acquire an HTTP framework, a command-line framework, or any
  provider SDK. The library MUST remain importable and usable with none of them installed.
- **FR-054**: The pipeline layer MUST NOT be reachable from any layer below it, and the command-line
  and HTTP layers MUST NOT import each other.
- **FR-055**: The constitution's statement of the layer chain MUST be amended in the same change that
  adds these layers to the enforced contract, as Principle X requires.
- **FR-056**: Every new capability MUST ship with documentation and at least one runnable example,
  and the offline examples MUST keep requiring no credentials.

**Closing the last governance debt**

- **FR-057**: The versioning policy that `TODO(PRE_1_0_VERSIONING)` has held open since the founding
  MUST be decided in this milestone and recorded as an ADR: what the `0.x` line promises, what it
  does not, and which surfaces — if any — are stable before `1.0.0`. The constitution's
  open-decision table MUST be moved from *Still open* to *Resolved* in the same change, as its own
  amendment procedure requires. This changes no runtime behaviour; it is the last unresolved
  constitutional decision, and this is the last milestone able to carry it.

### Key Entities

- **Pipeline**: The processor that sequences the four stages for one document. Carries a stable
  identity and a `pipeline_version` folded into the terminal artifact.
- **Stage**: One explicit, identified, versioned step — parse, extract, ground, validate — with its
  processor identity, its version, and the inputs folded into its options hash.
- **StageOutcome**: What happened at one stage: executed or reused, its duration, its resulting
  artifact identity, and where it failed, the stage and the typed error class — never the message.
- **PipelineResult**: One run's four stage outcomes, the results they produced, the terminal artifact
  identity that is the run's `processing_id`, and the provenance of the whole run.
- **ArtifactStore**: The append-only, content-addressed store of stage results. Optional, clearable,
  and required to be correct rather than fast.
- **ArtifactRef**: An artifact's identity together with the stage that produced it and the identity
  of its input — the edge of the chain, which is what makes a derivation explainable.
- **ArtifactEnvelope**: A stored artifact plus the artifact-format version it was written under and
  the identity it is stored against, so an incompatible or corrupted entry is detectable rather than
  silently reusable.
- **Derivation**: The explanation of one identity — its stage, its input identity, its processor and
  version, and the inputs folded into its options hash — carrying no document content.
- **BlobRef**: Submitted source bytes stored under their blob identity, so a document can be
  submitted once and processed later.
- **Job**: A requested run, identified by its terminal artifact identity, with a status drawn from a
  closed set. A record of a synchronous act, not a queue entry.
- **RunLimits**: The maximum document size, the maximum request size, and the accepted document
  types, enforced before any parse and any provider call.
- **StageEvent**: The one structured log event per stage execution — request identity, processing
  identity, step identity, duration, outcome, and where applicable provider, model, token usage, and
  whether the stage was reused.
- **Document / ExtractionResult / GroundingResult / ValidationResult / EvaluationReport** *(existing,
  Milestones 1–6)*: The artifacts this milestone sequences, stores, reuses, serialises, and prints.
  Their shapes are inputs to this feature and MUST NOT be redefined by it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a fresh checkout with no credentials and no network, one command produces a result
  in which 100% of extracted values carry a page and, where geometry exists, a bounding box.
- **SC-002**: A second identical run of the same document executes zero stages and produces a result
  byte-identical to the first.
- **SC-003**: After a change confined to the prompt or the schema, the parse is executed zero times
  and every stage from extraction onward is executed exactly once.
- **SC-004**: 100% of runs report, per stage, whether it was executed or reused, so the cost of any
  run is answerable from its own output.
- **SC-005**: An artifact whose content does not match its identity, and an artifact written under an
  incompatible format version, are returned as reusable results in 0% of attempts.
- **SC-006**: A run's terminal identity is recomputable from the inputs the run recorded in 100% of
  runs, with no field required that the run did not record.
- **SC-007**: For any identity a run produced, the derivation names the stage, the input identity,
  the processor, its version, and the folded inputs — in 100% of cases, and with 0% of them
  containing document content or credentials.
- **SC-008**: Across a run over a document containing known distinctive strings, 0% of those strings,
  0% of extracted values, 0% of credentials, and 0% of prompt bodies appear in the emitted logs,
  while 100% of the constitutionally required log fields are present.
- **SC-009**: A document exceeding the size limit or carrying a disallowed type results in zero
  parses and zero provider calls, and leaves zero temporary files behind.
- **SC-010**: A result fetched over HTTP and the same result computed in-process agree on 100% of
  values, verdicts, locations, and identities.
- **SC-011**: 100% of failures — at every stage, over every interface — surface as typed,
  provider-neutral errors; 0% of untyped exceptions reach a caller.
- **SC-012**: A run that fails at stage *n* returns the results of all *n−1* preceding stages in
  100% of cases.
- **SC-013**: The base install remains importable and the full offline test suite passes with no HTTP
  framework, no command-line framework, and no provider SDK installed.
- **SC-014**: The stage sequence parse → extract → ground → validate is expressed in exactly one
  place in the repository, verified by the recording step producing identical prediction sets before
  and after it is rewritten to call the pipeline.
- **SC-015**: The evaluation of the committed golden set, run through the command line with the store
  enabled, performs zero repeated parses across repeated runs — the cost the store exists to remove.
- **SC-016**: At merge, the constitution lists zero decisions as still open, and every one of them
  points at an accepted ADR.

## Assumptions

- **The final milestone is Milestone 7 as the roadmap states it**: pipeline, artifact store, command
  line, and HTTP interface. The founding document's further "Milestone 8 — Packaging" is settled as
  dropped on purpose (Clarifications, 2026-08-22): PyPI packaging, documentation, examples, tests,
  and CI already exist, and the one item that did not — a container image — is out of scope.
- **Execution is synchronous and single-node.** The constitution's deferred-technology list forbids
  queues, workers, and DAG engines in the MVP, and Principle XI requires only that today's local
  synchronous execution be able to *become* API → queue → workers later without changing the domain
  model. A job is therefore a record of a synchronous run, not a queue entry.
- **A job's identity is the terminal artifact identity**, per ADR-0003. This is what makes a job
  model possible with no database and no queue: status and result are answered from the store.
- **The store is a local filesystem store by default.** The sanctioned stack permits local filesystem
  or S3-compatible storage and PostgreSQL "only where persistence is genuinely required"; a
  content-addressed, append-only, immutable store has no requirement a filesystem cannot meet. An
  S3-compatible implementation is a later addition behind the same boundary, not a redesign.
- **There is no authentication, authorisation, tenancy, quota, or rate limiting in the HTTP
  interface.** The constitution names multi-tenant billing as deferred and is silent on
  authentication; the assumption is that a deployment places docdoc behind its own gateway. This is
  stated so it is a decision rather than an oversight, and it is the reason FR-044 exists.
- **The HTTP interface and the command line ship as optional extras**, for the same reason every
  provider SDK does: the base install stays deterministic layers and nothing else.
- **The stage set is fixed at the four stages that exist.** No stage is added, removed, or made
  configurable by this milestone.
- **Evaluation and recording are reachable from the command line but are not redesigned here.** The
  recorder changes only in that it calls the pipeline (FR-009); its outputs do not change (SC-014).
- **The artifact-format version is a new, explicit identifier**, not the package version. Tying
  reuse to the package version would invalidate every artifact on every release; tying it to nothing
  is FR-015's failure mode.
- **"Inspect" reads a result, not a document.** The inspect command shows values, verdicts, pages,
  and rectangles; rendering an image of the page with boxes drawn on it is not part of this feature.

## Dependencies

- **ADR-0003 — the per-stage content-addressed artifact chain.** This is the spine of the milestone.
  It supplies the identity formula, the per-stage options-hash inputs including its Milestone 5
  amendment, the rule that `pipeline_version` is folded into the terminal artifact, the rule that the
  job's `processing_id` is the terminal artifact identity, the append-only immutability of the store,
  the deferral of garbage collection, and the obligation to provide a tool that explains a derivation.
  Roughly half of this specification is that ADR finally becoming code.
- **ADR-0002 — blob and document identity.** Supplies the two-level identity FR-021 stores against
  and the canonical serialisation rules every options hash obeys.
- **ADR-0006 — the comparison-time match view.** Specifies the cache key FR-020 requires and that
  grounding does not currently honour.
- **ADR-0008 — schema evolution.** Fixes which schema changes move which identity, and therefore what
  FR-013's partial reuse does when a schema is edited.
- **ADR-0004, ADR-0005** — fix that model confidence routes nothing and that fuzzy grounding is
  pinned by version; both are inputs this milestone folds into identities and must not reinterpret.
- **Milestones 1–5** — supply every stage this milestone sequences, their typed errors, their
  provenance conventions, and the result shapes FR-034 serialises without altering.
- **Milestone 6 (`006-golden-set-evaluation`)** — supplies the evaluation command surface FR-026
  exposes, and the recording step FR-009 rewrites. Its `record.py` module docstring is the written
  record of the store's absence and is the closest thing this milestone has to a prior art note.
- **Constitution v1.4.0** — Principles X, XI, and XII bind this feature most directly, along with the
  MVP Scope Constraints on the sanctioned stack, deferred technology, security, the error model, and
  observability. FR-055 requires amending Principle X in the same change that extends the enforced
  layer contract.
- **Two new ADRs are expected, and the next free numbers are 0010 and 0011.** The first covers the
  store's on-disk layout, the artifact-format version, the synchronous job model, and the decision
  not to require a database — each a choice a later reader would otherwise have to reconstruct from
  code. The second is the versioning policy FR-057 requires, which resolves
  `TODO(PRE_1_0_VERSIONING)` and is the only dependency here that is governance rather than design.

## Out of Scope

Explicitly deferred, and MUST NOT appear in this feature:

- Queues, brokers, workers, background execution, schedulers, retries across processes, and any
  asynchronous job execution. A job here is a synchronous run with an identity.
- Kafka, Temporal, Kubernetes, multi-region deployment, distributed DAG engines, vector databases,
  RAG infrastructure, workflow engines, and autoscaling.
- A generic or user-configurable pipeline graph, conditional stages, custom stages, and plugin
  execution.
- PostgreSQL, any relational schema, migrations, and an object-store driver.
- Authentication, authorisation, multi-tenancy, quotas, rate limiting, billing, and usage metering.
- Webhooks, callbacks, streaming responses, server-sent events, and long-polling.
- Batch endpoints, bulk upload, and multi-document jobs.
- A review interface, an annotation workflow, a hosted result browser, and any rendering of a page
  image with bounding boxes drawn on it.
- Garbage collection, retention policy, and eviction for the artifact store.
- Distributed or shared caching, cache warming, and cross-host store coordination.
- Any change to parse, extraction, grounding, or validation *behaviour*. Adding a cache to grounding's
  match view (FR-020) must change performance and nothing else; if it changes a result, it is a bug
  in this milestone.
- Any change to the metric definitions, the golden set, or what evaluation counts.
- Automatic model selection, prompt tuning, and threshold search driven by anything this milestone
  measures.
- Detecting a processor whose output changed without a version bump. ADR-0003 assigns this to review;
  this milestone makes it inspectable and recoverable, not automatic.
- A container image, a container build, a compose file, and any release or publication process. The
  founding document's "Milestone 8 — Packaging" is closed as already-delivered-or-dropped, not
  reopened here (Clarifications, 2026-08-22).
- Flipping the constitution's evaluation gate from advisory to blocking.
