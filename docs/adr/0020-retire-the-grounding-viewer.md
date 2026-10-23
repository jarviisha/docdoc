# ADR-0020: Retire the Grounding Viewer; `ui/` Builds the Operations Console Only

- **Status**: Accepted; partly superseded by ADR-0023
- **Amended in part by**: [ADR-0027](0027-service-only-surface.md) — the Docker image now carries the console's build, so compose no longer mounts a host `ui/dist`; the wheel still carries no static asset
- **Date**: 2026-09-15
- **Supersedes**: the browser surface of `specs/008-grounding-viewer` (its view-model boundary, no-persistence rule, and asset-location machinery are kept and now serve the console)
- **Relates to**: [ADR-0012](0012-storeless-extraction-over-http.md) (the storeless route the viewer used, which is unchanged), [ADR-0019](0019-operations-console-boundary.md) (the console this leaves behind, and §5's re-pointing of the read-only guard)
- **Principles engaged**: XI (MVP discipline), XII (boundaries enforced by tests rather than convention)

## Context

`ui/` held two applications built from one dependency tree: Milestone 8's **grounding viewer** at
`/ui`, and Milestone 11's **operations console** at `/console`. They shared a transport, a component
library, a test runner, and a packaging distribution, and they shared almost nothing else — two vite
configs, two entry points, two model trees, two allow-lists, two mounts, two environment variables,
two asset directories inside one wheel.

The viewer's own documentation recorded the problem it had:

> **Turning [authentication] on does not secure the viewer — it removes it.** `/ui` then requires a
> credential like everything except the two health probes, and a browser cannot send a bearer token,
> so the interface becomes unavailable rather than protected.

That is the honest description of a surface that works on exactly one kind of deployment: the
unauthenticated one. Every deployment that turned on authentication — which is every deployment the
console exists for — had a `/ui` that answered `401` at the door.

The console solved the same problem and solved it properly: its shell is exempt from the credential
because the key is pasted **into a page that has already loaded**. The viewer could not adopt that
fix, because its shell is not where a credential is entered — it has no credential entry at all.

The repository was therefore carrying two browser applications, one of which could not be used on an
authenticated deployment, and paying for the pair in every shared file. `check-readonly.mjs` had
already had to narrow its claim from "this repository's UI edits nothing" to "**the viewer** edits
nothing" (ADR-0019 §5) because the console next door is full of forms. `vite.console.config.ts`
existed as a second config specifically because the viewer occupied `base: "/ui/"` and the filename
`index.html`. `docdoc.api.ui` grew a `Surface` named tuple to parametrise one search over two
surfaces.

## Decision

**`ui/` builds the operations console and nothing else.** The grounding viewer is removed: its
sources, its tests, its mount, its environment variable, its entry in the `docdoc-ui` distribution,
and its concept document.

### 1. What is deleted

| Removed | Was |
|---|---|
| `ui/src/components/`, `ui/src/model/`, `ui/src/main.tsx` | the viewer, ~2000 lines |
| `ui/vite.config.ts` (the `base: "/ui/"` one) | the viewer's build |
| `ui/scripts/check-readonly.mjs` | the read-only fence — see §3 |
| `_mount_ui` in `docdoc/api/app.py`, `/ui` | the mount |
| `UI_ROOT_ENV`, and the environment variable it held | where to find the viewer's build |
| `docdoc_ui.ASSETS`, `src/docdoc_ui/assets/` | the viewer's half of the wheel |
| `docs/concepts/viewer.md` | replaced by `docs/concepts/console.md` |

`/ui` is now a `404`. It is not a `501` and not a gate: there is nothing there.

The removed environment variable is named here as its Python constant, `UI_ROOT_ENV`, and not as the
`DOCDOC_`-prefixed string it held. That is not delicacy:
`test_no_document_names_a_setting_outside_the_checked_or_recorded_set` sweeps every document for
settings by that prefix and requires each naming document to be checked against the code, and
`test_every_documented_configuration_name_exists` then requires every setting so named to still
exist. A retired variable spelled out in full would fail the second, and an exemption list was
considered and rejected when those checks were written. Writing the constant keeps both checks honest
and still tells a reader searching their own deployment's configuration what is gone.

### 2. What is kept, because it was never the viewer's

Three things came from `specs/008` and outlive it, and naming them is the point of this section —
deleting a feature is where its still-load-bearing parts get thrown out by association.

- **The view-model boundary** (`check-model-boundary.mjs`). The rendering layer carries no automated
  test, so the model is the entire tested surface. That is as true of the console as it was of the
  viewer, and the console has more to lose: it sends a credential.
- **The no-persistence rule.** See §3.
- **The asset-location machinery** (`docdoc.api.ui`): three roots, a documented precedence, a
  checkout that beats an installed copy, and four different sentences for four different absences.
  It was written for the viewer and every word of it applies to the console.

### 3. The read-only guard is split, not deleted

`check-readonly.mjs` enforced two unrelated claims that happened to share a file:

| Half | Claim | Fate |
|---|---|---|
| `NO_EDITING` over `src/components/` | the viewer renders and never edits (`specs/008` FR-029) | **deleted** — the subject is gone |
| `NO_PERSISTENCE` over all of `src/` | nothing comes to rest in the browser (`specs/011` FR-007) | **kept**, as `check-no-persistence.mjs` |

Deleting the file wholesale would have taken the second half with it, and the second half is the more
valuable one: it is what keeps the console's API key out of `localStorage` and makes SC-004's "zero
recoverable bytes" a check instead of an intention. It gains a cookie rule while being rewritten,
because ADR-0019 §4 rejected a cookie session by name and nothing was watching for one.

### 4. `specs/011`'s three viewer clauses are retired with it

FR-022b ("the viewer MUST NOT be modified by this milestone"), FR-040 ("Milestone 8's read-only
guarantee MUST remain machine-checked over the viewer's own sources"), and SC-012 ("the viewer's
read-only check still passes") are all requirements **about Milestone 11's restraint**, discharged
when Milestone 11 shipped. They are not requirements about the repository in perpetuity, and a later
decision to remove the viewer does not violate a rule that said "do not touch it *while building the
console*".

They are marked retired in the spec rather than deleted, with a pointer here. A requirement that
quietly disappears is how a spec becomes unreadable as a record of what was decided.

### 5. The console keeps `/console`

Not moved to `/ui` despite the prefix being free. It is named in `specs/011` FR-001, it is in
operators' bookmarks, and it contains none of the fifteen words
`tests/contract/test_no_review_platform.py` forbids in a path. Reclaiming four characters is not
worth a redirect nobody asked for.

The build output directory **is** renamed, `ui/dist-console` to `ui/dist`: it was named for the
`dist` it sat beside, and with nothing beside it the qualifier described a distinction that no longer
exists. The installed distribution keeps `console/` and `CONSOLE_ASSETS` — a developer's machine can
still be carrying a gitignored `assets/` holding a built viewer, and a console that answered to that
name would serve it. `build.sh` deletes any it finds.

## Consequences

**A stored run's grounding cannot be inspected in a browser.** This is the real cost and it is not
small. It was already close to true — `specs/011`'s Out of Scope called opening a stored run in the
viewer "the most-wanted thing on this list" and recorded that it was never possible: the viewer holds
a two-path allow-list and no route serves a stored document's bytes. What is actually lost is
inspecting a document **you have on your own disk**, by picking it in a file dialog. `POST
/v1/extract` still does exactly that over HTTP, and `examples/view_grounding.md` shows the grounding
result it returns.

**A future grounding surface is not blocked, and would be cheaper than this one was.** The parts that
made the viewer expensive — the model boundary, the no-persistence guard, the three-root asset
search, the packaging split — are all still here and all still tested. What would be new is a
renderer and the read it needs. A milestone that wants one adds `GET /v1/documents/{blob_id}` serving
bytes, and restates `specs/011` SC-003's "exactly one new server-side concept" as the historical
claim it is.

**`pdfjs-dist` leaves the dependency tree**, and with it the reason `ui/LICENSES.md` was first
written. The file stays: the obligation it guards was never specific to one package.

**The base install is unchanged.** It acquired no static asset before and acquires none now; the
`ui` extra still delivers `docdoc-ui`, which now carries one surface instead of two.

## Alternatives considered

**Keep the viewer, stop building it by default.** Rejected. It leaves ~2000 lines that nothing
builds, nothing serves, and no guard covers — and `check-readonly.mjs` would have had to keep
scanning a tree nobody compiles, reporting "clean" about code that cannot be wrong because it cannot
run. Dead code that still passes its tests is the worst of both: the maintenance cost of keeping it
and none of the confidence.

**Fix the viewer's authentication instead.** Rejected, and this is the one worth stating. It is
technically possible: give the viewer a credential-entry page like the console's and exempt its shell
the same way. But the result is a second application that pastes a key, holds it in memory, and loses
it on reload — with a second copy of the session model, the failure vocabulary, and the idle bound.
ADR-0019 §4's argument against issuing a second credential type applies to building a second
credential *handler*. If a grounding surface returns, it returns inside the console's session, not
beside it.

**Merge the viewer into the console as a fourth area.** Rejected for now rather than on principle,
and it is the shape a future grounding surface should take. It is not this change: it needs the
document-bytes read, it widens the console's allow-list from nine endpoints to ten, and it puts a
result renderer inside an application whose `check-console-boundary.mjs` currently fails the build if
any part of a result is rendered (`specs/011` FR-022a). Each of those is a decision with its own
argument, and none of them is "delete the thing nobody can use".
