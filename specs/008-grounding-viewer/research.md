# Phase 0 Research: Read-Only Grounding Viewer

**Feature**: `008-grounding-viewer` | **Date**: 2026-08-25

Ten questions the plan could not be written without. R1 and R7 changed the design; the rest confirmed
that most of this milestone is smaller than it looks, because the engine already returns what a viewer
needs in the form a browser wants it.

---

## R1 — Are normalized coordinates in unrotated or displayed page space?

**Decision**: **Displayed space.** The viewer renders each page through the renderer's default
viewport, which already applies the page's `/Rotate`, and draws normalized boxes onto it directly.
`Page.rotation` is provenance and **MUST NOT** reach the overlay or any transform.

**Rationale**: measured, not assumed. `src/docdoc/ingest/parsers/pdf_text.py` maps every word box
through `page.rotation_matrix` *before* normalizing it, and normalizes against `page.rect`, which is
the page as displayed — its own docstring says so: "PyMuPDF reports word boxes in *unrotated* page
space while `page.rect` is the *displayed* size. Normalizing one against the other would place every
box wrongly on a rotated page." The other two parsers agree by a different route: `gcv.py:176` records
that "the service reports coordinates as displayed, so there is no rotation" and sets `rotation=0`, and
`azure_di.py:199` sets `rotation=0` while noting that its `angle` is estimated skew rather than page
rotation.

So every box that reaches a caller is already oriented the way the page is shown. Since the ingest
layer resolved rotation, the viewer's correct action is to do nothing.

**Alternatives considered**: reading `Page.rotation` and rotating the overlay to match. Rejected — it
double-applies a rotation the parser already applied, and it fails *only* on rotated pages, which is
precisely the population FR-024 exists to protect. This is the most plausible wrong implementation of
this feature and the reason FR-023 forbids any transform beyond scaling.

---

## R2 — What renders a PDF page in the browser, and under what licence?

**Decision**: `pdfjs-dist` (Mozilla's pdf.js), **Apache-2.0**, rendering to a canvas per page.

> **Astryx's concrete packages**, resolved 2026-08-25 after `/speckit-analyze` found this section named
> only `@astryxdesign/*`, which is not installable: **`@astryxdesign/core`** (the components) and a
> theme package such as `@astryxdesign/theme-neutral`, with **`@astryxdesign/cli`** as a dev
> dependency. Two consequences for the plan. It ships **pre-built CSS and JS and needs no build
> plugin or PostCSS**, which confirms that StyleX stays an implementation detail we never configure.
> And it requires **React ≥19** as a peer dependency — a constraint rather than a choice, and the only
> version floor this milestone has.

**Rationale**: the licence is the decision. FR-039 forbids acquiring an obligation incompatible with
Apache-2.0, and the rejected server-side alternative would have made PyMuPDF — AGPL-3.0, and behind an
opt-in extra for exactly that reason (`pyproject.toml:41`) — a required path rather than an opt-in one.
pdf.js also supplies the viewport abstraction R1 depends on, so rotation stays the renderer's business
and never becomes ours.

**Alternatives considered**: server-side rendering (rejected in the spec's first clarification, on
licence and on the "never the bytes" boundary); native browser PDF embedding (gives no per-page canvas
to overlay, and no page-level control for R3).

---

## R3 — Which pages get rendered, and how is the bound expressed?

**Decision**: render the pages the result names; keep every other page reachable and render it on
navigation. The bound is *the count of pages carrying located values*, not a page cap.

**Rationale**: the deployment's default page limit is **1000** (`DEFAULT_MAX_PAGES`,
`src/docdoc/ingest/source.py:66`), so a thousand-page document is ordinary rather than abusive, and
eager rendering fails on documents docdoc itself accepts. Bounding by result rather than by an
arbitrary page cap means the viewer's cost tracks the thing it exists to show.

**Alternatives considered**: virtualized scrolling over all pages — the general answer, and the most
machinery, all of which would be untested under clarification 1; a fixed page cap — arbitrary, and
silently truncates a document the deployment accepted.

---

## R4 — How is the view model tested with no browser and no simulated DOM?

**Decision**: Node's built-in test runner (`node --test`) with `node:assert`. **No new test dependency.**

**Rationale**: clarification 1 excluded browser automation and simulated DOMs. What remains is
ordinary unit testing of pure functions, which Node has shipped natively since 18. Adding a framework
whose principal draw is the jsdom/browser mode we just excluded would be paying for the part we
rejected. It also keeps the promise of FR-042 literally checkable: a view model that needs a test
framework's environment is not framework-free.

**Alternatives considered**: Vitest or Jest — both add a dependency and configuration; their value here
would be the DOM environment, which is out of scope by decision.

---

## R5 — What shape does the storeless endpoint take?

**Decision**: `POST /v1/extract?schema=<identity>` carrying the raw document bytes, returning the same
result body the store-backed route returns, minus the job identity. It passes `NullArtifactStore()`
**unconditionally**, regardless of deployment configuration.

**Rationale**: every piece already exists. `POST /v1/documents` reads a raw body through
`_read_capped`; `POST /v1/documents/{blob_id}/extract` takes `schema` as a query parameter; and
`pipeline.run()` takes bytes plus a `store`, which `app.py:229` already calls that way. The new route
is those pieces assembled with a different store argument, which is what makes FR-008 cheap to keep:
passing `NullArtifactStore()` unconditionally means the *endpoint* decides whether a run persists, not
the deployment, and there is no configuration under which this route writes.

Limits reuse the submission path exactly — `detect_media_type` from the bytes, `SourceFile.from_bytes`,
`check_limits` — so FR-005 is satisfied by calling the same code rather than by restating the rule.

**Alternatives considered**: multipart upload (adds a parser dependency and a second body convention
for no gain, since one document is one body); a `schema` field in a JSON envelope with base64 bytes
(inflates the body by a third and puts a document through a JSON encoder).

---

## R6 — How does the deployment list its schemas?

**Decision**: project `SchemaRegistry.identities()`. No new logic.

**Rationale**: `src/docdoc/extraction/registry.py:151` already returns a sorted tuple of `name@version`
identities, and `resolve()` accepts exactly those strings — which satisfies FR-010 ("identify each
schema by the name an extraction request accepts") by construction rather than by translation. The
identities carry no filesystem information, so FR-011 holds without filtering. An empty registry
returns an empty tuple, which the endpoint returns as an empty list rather than an error (FR-012): a
deployment with no schemas configured is validly configured.

**Alternatives considered**: returning `describe()` output with field lists — more than the viewer needs
to offer a choice, and it exposes schema internals over an unauthenticated endpoint (R10).

---

## R7 — How are built assets served without the base install acquiring them?

**Decision**: the client's **source** lives in `ui/` in this repository; the **built assets** ship as a
separate distribution that the `ui` extra depends on, pinned with `==` to the exact `docdoc` version it
was built against — `ui = ["docdoc-ui==0.1.0"]`, moving in lockstep, because assets and the routes that
serve them are one artifact split for packaging reasons and not two things with a compatibility
range. `docdoc.api`
locates it by import and serves it from the same origin. The repository commits no build output.

**Rationale**: this was the one design question with no comfortable answer. Three requirements collide:
the base install must acquire no static asset (FR-035), build output must not be committed (FR-038),
and the assets must be served same-origin (FR-034). A Python extra can add a *dependency* but cannot
add *files* to a wheel everyone already installs — so assets in the main wheel reach every
`pip install docdoc`, which FR-035 forbids in as many words. A separate distribution is the only
arrangement found where opting in is what delivers the files.

It also keeps the third founding decision intact: the source stays in this repository, versioned and
reviewed with the endpoints it depends on. Only the artifact is separate.

**Alternatives considered**: assets in the main wheel (breaks FR-035); committing `dist/` (breaks
FR-038); building at install time (requires Node on the user's machine and makes `pip install` execute
a JavaScript toolchain); an operator-supplied asset directory named by a setting — this works, needs no
second distribution, and is **retained as the fallback** if publishing a second artifact proves
disproportionate, but it is not the default because `pip install docdoc[ui]` would then install no
interface, which is a worse failure than the one it avoids.

---

## R8 — How is "the base install acquires nothing" kept true?

**Decision**: extend `tests/unit/test_base_install_excludes_evaluation_data.py`'s technique to the new
extra: read `pyproject.toml` with `tomllib`, assert the wheel still ships `["src/docdoc"]` and nothing
UI-shaped, assert the `ui` extra's requirements appear in no base dependency, and assert that importing
`docdoc` on a base install neither imports nor requires the asset module.

**Rationale**: the existing guard exists because FR-059 of Milestone 6 "currently holds by build
configuration rather than by rule, and that is exactly the kind of guarantee that erodes silently" —
the same sentence applies here word for word, and the same test file already knows how to check it.
Principle XII requires dependency boundaries to be enforced by an automated test rather than by
convention, and this is that test.

**Alternatives considered**: trusting the packaging configuration — which is what the existing test was
written to stop doing.

---

## R9 — What stops decisions leaking back into the rendering layer?

**Decision**: an automated boundary check over `ui/`: `src/model/**` may import nothing from
`src/components/**`, nothing from React, and nothing from the renderer. Violations fail the build.

**Rationale**: FR-043 is the requirement that decides whether clarification 1's accepted gap stays
small or swallows the feature, and a requirement of that weight cannot rest on review. This repository
already holds the position that boundaries are machine-checked — Principle XII says so, and
`import-linter` enforces the Python layer graph today. The browser client gets the same treatment for
the same reason, and the check is cheap because the rule is one-directional and has no exceptions.

**Alternatives considered**: code review (the convention Principle XII names as insufficient); a lint
rule on file naming (checks names, not imports).

---

## R10 — What does the corrected `http-api.md` have to say?

**Decision**: rewrite section 1's store table to match the code, and replace the sentence "Running an
extraction and reading what it produced do not [need a store]" with an accurate account: the
`blob_id`-shaped route needs one because a `blob_id` only exists after a submission, and the new
storeless route is the path that needs none. State that a storeless run has no terminal artifact and
therefore no job to fetch, and record that a deployment configured without a store now serves
extractions it previously refused (FR-063).

**Rationale**: FR-007 requires the contract to stop contradicting the code, and there were only ever
two ways to end the disagreement. Weakening the sentence would have documented the store coupling as
intended, in a project that rejected Vision's asynchronous API for creating "a place for document
content to come to rest outside the process" — the same objection, applied to our own interface with
more force because the resting place is our decision.

**Alternatives considered**: leaving Milestone 7's contract untouched and describing the new endpoint
only in this milestone's contract. Rejected: the false sentence would remain, and a reader would find
two contracts disagreeing instead of one contract disagreeing with the code.
