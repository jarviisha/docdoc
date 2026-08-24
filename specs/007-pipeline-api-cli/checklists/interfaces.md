# Interface & Failure-Semantics Requirements Quality Checklist: Milestone 7

**Purpose**: Unit-test the *requirements* governing the two entry points and the failure semantics
they share — are they complete, unambiguous, mutually consistent, and objectively verifiable — before
implementation of US1, US3, and US5 proceeds.
**Created**: 2026-08-24
**Feature**: [spec.md](../spec.md) · [plan.md](../plan.md) · [contracts/cli.md](../contracts/cli.md) ·
[contracts/http-api.md](../contracts/http-api.md) · [contracts/pipeline-api.md](../contracts/pipeline-api.md)

**Depth**: formal gate. This must be resolved before the US1/US3/US5 tasks are implemented.

**Status**: **blockers cleared, 2026-08-24.** The six items that changed behaviour rather than wording
— CHK002, CHK019, CHK020, CHK021, CHK022, CHK032, CHK033 — are resolved and recorded inline below.
Three requirements added (FR-066, FR-067, FR-068), two amended (FR-035, FR-036), one ADR amended
(ADR-0010), three tasks rewritten (T069, T070, T077) and three added (T108, T109, T110).
Implementation of the remaining tasks proceeded on that basis. The 32 open items are wording,
measurability, and traceability; they do not gate code and are listed as found.

**Scope**: spec.md, plan.md, and all three contract documents. Caching and identity are out of scope
here — [checklists/caching.md](caching.md) closed them on 2026-08-22.

**Why this domain**: reuse was the milestone's only new *mechanism* risk, and it has been unit-tested.
The remaining risk is a different shape: these requirements are elaborated in **three separate contract
documents**, and the spec's Edge Cases demand that "the command line, the HTTP interface, and the
recorder must agree on what is returned". A rule that is merely vague here does not fail a test — it
gets read three ways and implemented three ways, and the disagreement surfaces only when someone
compares two surfaces that were never compared. Several already disagree on the page.

---

## Requirement Completeness

- [ ] CHK001 Are requirements defined for the **blob store's** operations and stored metadata? FR-021 requires storing submitted bytes under their blob identity and `GET /v1/documents/{blob_id}` promises identity, size, and media type — but the only store surface in the contracts is the artifact store's four operations. [Gap, Spec §FR-021, Contract pipeline-api §5, Contract http-api §1]
- [x] CHK002 Is it stated whether the HTTP interface **requires** a configured store, and how a deployment configures one? FR-017 makes the store off by default with no default location, while every job endpoint is defined as a store lookup — so an unconfigured deployment answers every lookup with `unavailable` and has nowhere to write a blob. [Gap, Spec §FR-017, Contract http-api §3]
      → **Added FR-068**, resolved in passing with CHK032 because the two are one question. Submission and the job endpoints need a store; running an extraction and reading its result do not, now that FR-067 returns the result inline. A submission with no store configured is refused with an error naming the setting, rather than accepting bytes that cannot be kept. T110 added.
- [ ] CHK003 Are requirements defined for how the HTTP service is **started** — a runnable entry point, or an explicit statement that starting it is the deployment's business? FR-026's minimum command set omits it and the CLI contract has no `serve`. [Gap, Spec §FR-026, Contract cli.md §1]
- [ ] CHK004 Is the error-to-status mapping required to be **exhaustive** over the constitution's error model? `DocumentError` and `ParserError` are named by the constitution and have no row, so their status is unspecified. [Gap, Contract http-api §6, Constitution §Error model]
- [ ] CHK005 Are requirements defined for the HTTP **status codes of job-lookup outcomes**? The error table covers failures only; nothing states whether `unavailable` is a 200 carrying a status body or a 404, which a client must know to write a correct branch. [Gap, Contract http-api §3, Contract http-api §6]
- [ ] CHK006 Is the outcome specified when a client **disconnects or the request times out** mid-run? FR-041 names "aborted" for temporary files only; nothing states whether the run completes, whether its artifacts are written, or whether the resulting job id is recoverable. [Gap, Spec §FR-041, Spec §Edge Cases]
- [ ] CHK007 Is the **recorder's** rendering of a partial failure specified anywhere? The Edge Cases require three surfaces to agree and only two of them have a contract document. [Gap, Spec §Edge Cases, Spec §FR-009]
- [ ] CHK008 Does the spec state that the eval command's output distinguishes a **partial** report from a full one? ADR-0009 requires a run without the restricted tier to be marked partial, and FR-026 exposes evaluation without restating it. [Gap, Spec §FR-026, ADR-0009]
- [ ] CHK009 Are requirements defined for **concurrent runs** in the HTTP interface — a stated bound, or an explicit statement that there is none? The "an artifact fits in memory and is serialised whole" assumption is stated per-run, and an ASGI server serves requests concurrently. [Gap, Spec §Assumptions, Spec §FR-033]
- [ ] CHK010 Is the **shape** of the machine-readable output specified, or only that standard output carries exactly one JSON document? A caller parsing it has no stated field set and no stated version. [Gap, Spec §FR-027, Contract cli.md §2]

## Requirement Clarity

- [ ] CHK011 Is **"docdoc's own message"** defined as content-free? The HTTP error body carries it, while the pipeline result carries only a class name — explicitly "never its message, which can quote the document". Either the message is safe to return and the pipeline is over-restricted, or it is not and the HTTP body leaks. [Ambiguity, Contract http-api §6, Contract pipeline-api §4, Spec §FR-037]
- [ ] CHK012 Is the discriminator that splits `UnsupportedDocumentError` into **415 versus 413** specified? One error class maps to two statuses with no stated field to tell them apart. [Clarity, Contract http-api §6]
- [ ] CHK013 Is the boundary between exit code **`2` and `64`** defined? "Unreadable file" is listed under `64`, yet an unreadable file is exactly what raises a typed `DocumentError`, which the same table sends to `2`. [Ambiguity, Contract cli.md §3, Spec §FR-028]
- [ ] CHK014 Is the exit-code set stated to be **closed**? FR-028 says "at minimum" three and the contract defines four; a caller branching on codes cannot tell whether a fifth may appear in a later release. [Clarity, Spec §FR-028, Contract cli.md §3]
- [ ] CHK015 Is it specified **which stage a limits refusal names**? FR-037 requires every error to name the stage at fault, and size and type refusals are required to happen before any stage runs. [Clarity, Spec §FR-037, Spec §FR-039, Spec §FR-040]
- [ ] CHK016 Is "parses arguments, calls the pipeline, and **formats a result**" bounded? FR-030 forbids extraction, grounding, or validation logic in the CLI, while selecting exit `0` versus `1` requires the CLI to read and act on a verdict. [Clarity, Spec §FR-030, Contract cli.md §6]
- [ ] CHK017 Is the switch FR-064 requires given **one name**? The CLI contract calls it `--verify-cache`; the pipeline contract calls it `run(..., verify=True)`. FR-031 forbids a second, differently-named configuration vocabulary. [Consistency, Spec §FR-031, Contract cli.md §4, Contract pipeline-api §5]
- [ ] CHK018 Is the direction of FR-031's rule stated — every environment setting gains a flag, but is the converse required? `--verify-cache` has no environment setting, which is permitted only if the rule is one-directional. [Clarity, Spec §FR-031, Contract cli.md §4]

## Requirement Consistency

- [x] CHK019 **Do FR-035 and FR-036 require distinguishing two conditions the store cannot distinguish?** A never-produced id and an id whose artifacts were cleared under FR-019 are both simply absent from an append-only store that keeps no tombstone. `unknown` and `unavailable` are required to differ, and neither the spec, the contract, nor research R7 states a mechanism. This is the sharpest item on the list: T077 tests a distinction the design does not yet describe how to make. [Conflict, Spec §FR-035, Spec §FR-036, Spec §FR-019, Contract http-api §3, Research §R7]
      → **FR-035 amended; ADR-0010 amended (2026-08-24).** The distinction is dropped, not mechanised. `unknown` now means *not a well-formed artifact identity* — a syntactic judgement needing no history — and `unavailable` covers every well-formed absent id, cleared or never produced, and says plainly that it cannot tell which. Adding tombstones was the alternative and was rejected: it makes `clear()` a write, gives the store state, and buys a distinction no caller has a use for. T070 and T077 rewritten.
- [x] CHK020 Is `unknown` a member of the **closed status set**? The contract declares the set closed and tabulates two members, then introduces a third in the following paragraph. [Conflict, Contract http-api §3]
      → **Closed by CHK019's resolution.** The set is now closed at three and tabulated as three: `succeeded`, `unavailable`, `unknown`. The stray paragraph is gone.
- [x] CHK021 Does the HTTP error response carry the **preceding stages' results** FR-004 requires? The CLI contract states that exit `2` "carries the stages that succeeded"; the HTTP error body is specified as class name, stage, message, and structured detail, with no results. [Conflict, Spec §FR-004, Contract cli.md §3, Contract http-api §6]
      → **Added FR-066; http-api §6 now specifies the body.** A mid-run failure returns the typed error plus `outcomes` and `results`. Recorded alongside it: `results` legitimately carries extracted values, because it is the caller's own document returning on the caller's own request — FR-043's prohibition is about logs, and the rule holding in both places is that a *provider's* message never appears. T108 and T109 added.
- [x] CHK022 Can "a failed run produces no terminal artifact and therefore **no job**" coexist with FR-004's prohibition on discarding partial results? If no job exists, the partial result has no identity and no retrieval path, so over HTTP it is discarded in the only sense a caller can observe. [Conflict, Spec §FR-004, Contract http-api §3]
      → **Yes, once CHK021's body exists.** The absence of a job is kept — inventing an identity for a run that produced no terminal artifact would be a second identifier for nothing. What changes is that the response is now understood as *the only place* a partial result can appear, and is required to carry one. T109 asserts the CLI and HTTP agree on the stage, the class, and which results survive.
- [ ] CHK023 Is `ModelProviderError` part of the constitution's error model? FR-050 requires defining exactly two new typed errors and reusing the existing ones "rather than wrapping them in new names", and this name appears in the status table without provenance. [Consistency, Contract http-api §6, Spec §FR-050, Constitution §Error model]
- [ ] CHK024 Is a result retrievable **by identity** from the command line, as it is over HTTP? FR-026 requires a command to "inspect a result's values and their locations", while `inspect` is specified to take a file and a schema and re-run the pipeline. Someone holding a `processing_id` from a log has an HTTP path and no CLI path. [Consistency, Spec §FR-026, Contract cli.md §1, Contract http-api §1]
- [ ] CHK025 Is the equality FR-034 and SC-010 assert **scoped against the fields SC-002 exempts**? A result fetched from the store and one computed in-process necessarily differ on per-stage durations and executed/reused status; the equality is enumerated over values, verdicts, locations, and identities, but nothing states whether the serialised result carries the stage outcomes at all. [Consistency, Spec §FR-034, Spec §SC-002, Spec §SC-010]
- [ ] CHK026 Do the CLI and HTTP surfaces agree on whether **a document that fails validation is an error**? The HTTP contract states plainly that it is not; the CLI assigns it a non-zero exit code. Both are defensible, and nothing states that the divergence is deliberate. [Consistency, Contract cli.md §3, Contract http-api §6]

## Acceptance Criteria Quality

- [ ] CHK027 Is SC-011's population defined? "0% of untyped exceptions reach a caller" is only measurable against a stated set of injected failures across a stated set of surfaces. [Measurability, Spec §SC-011]
- [ ] CHK028 Is SC-012's "100% of cases" scoped to a defined set of failures? Without one, the criterion is satisfied by any single passing case. [Measurability, Spec §SC-012]
- [ ] CHK029 Does SC-010's comparison state its population — which documents, which schemas, and whether a **failing** run is included? The interesting half of FR-034's equality is the half CHK021 shows the two contracts describing differently. [Measurability, Spec §SC-010, Spec §FR-034]
- [ ] CHK030 Is there a measurable criterion for the **three-way agreement** the Edge Cases demand? SC-010 covers HTTP against in-process; SC-014 covers the recorder's prediction sets. Neither compares the recorder's failure rendering to either interface's. [Gap, Spec §SC-010, Spec §SC-014, Spec §Edge Cases]
- [ ] CHK031 Are the stdout/stderr discipline and the exit-code split represented in Success Criteria at all? FR-027 and FR-028 carry the milestone's most script-visible promises and no SC measures either. [Gap, Spec §FR-027, Spec §FR-028]

## Scenario & Edge Case Coverage

- [x] CHK032 Is the outcome specified when a run **succeeds but the terminal artifact fails to write**? FR-063 requires the run to return its result with the stage reported executed; over HTTP that hands back a job id whose immediate lookup reports `unavailable`, which FR-036 defines as the answer for a result that no longer exists. [Coverage, Spec §FR-063, Spec §FR-036, Contract pipeline-api §6]
      → **Added FR-067.** The run's response carries the result in full, so the caller never depends on the lookup for a run it just performed. A later `unavailable` is then correct rather than contradictory: it means the store does not hold it, which is exactly true. This also removes the same hole in the far more common case — no store configured at all, which is the default (FR-017) — where an identity-only response would have been unredeemable on every single call. T069 rewritten.
- [x] CHK033 Are requirements defined for a **malformed** job id, as distinct from a well-formed absent one? The status set is specified only for ids that are well-formed. [Coverage, Gap, Contract http-api §3]
      → **Closed by CHK019's resolution**, which is what `unknown` now means. `FileArtifactStore._digest_of` already refuses a non-hex identity, so the judgement exists in code and needed only a status to report it under.
- [ ] CHK034 Is behaviour specified when the **same document is submitted for extraction concurrently twice**? The Edge Cases cover concurrent writes at the store; nothing covers two in-flight requests producing them. [Coverage, Spec §Edge Cases, Spec §FR-062]
- [ ] CHK035 Are requirements defined for a `--json` run that both **emits a warning and fails partway**? The Edge Cases require standard output to stay parseable when a warning occurs; they do not address whether a failed run still writes exactly one document there. [Coverage, Spec §Edge Cases, Spec §FR-027]
- [ ] CHK036 Is the **missing-credentials** failure specified per surface? The Edge Cases require the failure to name the missing configuration rather than the document, and FR-059 requires a fully reused run to need none — so the same inputs fail or succeed depending on store state, and only the CLI's rendering is described. [Coverage, Spec §Edge Cases, Spec §FR-059]
- [ ] CHK037 Is the **empty-registry** error required over HTTP as well as from the CLI? US1 scenario 5 makes it a requirement, the CLI contract restates it, and the HTTP contract maps `SchemaError` to 400 with no equivalent obligation. [Coverage, Spec §US1, Contract cli.md §4, Contract http-api §6]

## Dependencies & Assumptions

- [ ] CHK038 Is the assumption that a deployment fronts docdoc with **its own gateway** carried by a requirement on the shipped documentation? It appears in Assumptions and in the HTTP contract's exclusions; FR-056 requires documentation and an example without naming it. [Assumption, Spec §Assumptions, Spec §FR-056]
- [ ] CHK039 Is the HTTP interface's **dependency on a configured store** recorded in Dependencies? The job model is defined entirely as store lookups, and the Dependencies section names ADRs and milestones but not this. [Dependency, Gap, Spec §Dependencies, Contract http-api §3]

## Notes

- Resolve each item by amending the spec or the contract it cites, and record the resolution inline
  beneath the item, as `caching.md` did. An item closed by argument rather than by an edit should say
  so and say why.
- CHK019, CHK021, CHK022, and CHK032 are the four that change behaviour rather than wording. They
  concern the same seam — what an identity means when the store does not hold it — and are best
  resolved together rather than one at a time.
- Items marked `[Conflict]` cite two documents that disagree today. Resolving one by editing only the
  weaker statement is what produced the disagreement in the first place; state which document is
  authoritative for the rule.
