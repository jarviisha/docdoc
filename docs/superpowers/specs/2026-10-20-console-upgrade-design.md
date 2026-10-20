# Console upgrade: from a key-pasting run list to the OCR gateway's control room

Status: approved. Date: 2026-10-20.

## Intent

docdoc is now an OCR gateway for mmerp (`2026-10-19-ocr-gateway-design.md`,
`2026-10-19-async-ocr-queue-design.md`). The people who run it check OCR and extraction runs every
day, and the console at `/console` does not let them: it does not know a run can be OCR, cannot
show what OCR produced, has no overview of what is waiting, running, or failing, and sees one
tenant at a time through a pasted API key. So operators leave the console for `curl` and logs.

The upgrade makes the console the place an operator opens first. At a glance it shows whether the
deployment is healthy, where any run is, why a failed run failed, and what OCR actually read, laid
over the page it read it from.

Success: an operator chasing an OCR or processing problem never has to leave the console for
`curl` or logs to find the run, see its timeline and error, or check its output against the page.

## Decisions the owner made

These came from the owner in the design conversation. They are not the builder's to revisit.

1. **Layout A: left sidebar with a dedicated Overview page.** The Overview is the landing screen and
   shows count cards (queued, running, failed in the last hour, done in the last hour, each split by
   kind), a list of recent failures, and a list of runs currently running. It refreshes itself.
2. **The console renders results.** ADR-0019's "the console renders no part of a result" is lifted.
3. **Result view B: the page image next to the OCR text, with a box per line.** Hovering a line
   highlights its box on the page. Lines with low `source_confidence` are highlighted. There is page
   navigation and a raw-JSON download. The owner chose this knowing it needs a new route that serves
   page images and brings back part of the grounding viewer that ADR-0020 retired.
4. **Username/password login. Logged in means you see the whole deployment.** That covers every
   tenant, every run, and every admin area, at one level of access with no role layers. Pasting an
   API key into the console goes away. Tenant API keys stay exactly as they are for machine callers
   such as mmerp.
5. **Multiple console accounts.**
6. **Accounts are managed from the CLI for now.** There is no user-management screen.
7. **A failed run records a content-free failure reason.** Today the worker stores only the
   exception class (for example `ParserError` at `parse`) and deliberately drops the message,
   because it can carry document text. So an operator cannot tell a host timeout from a host 5xx
   from a rejected language. Add a reason drawn from a fixed, closed set, roughly: `timeout`,
   `host_error`, `host_rejected`, `unsupported_input`, `other`. The final set is the builder's. The
   worker sets it, it is stored on the run, and run detail and the recent-failures list show it.
   It never carries free text, so the no-content rule holds. If the constitution or an ADR words
   that rule in a way that needs touching, the governance ADR covers it.

**Rotation (the designer's recommendation, approved with the document).** PaddleX corrects page
orientation before it reads (`paddlex.py` `_oriented_size`, `doc_preprocessor_res.angle`), so the
boxes are in the corrected frame. The angle is not persisted, and the stored `Document` cannot tell
90 from 270, or show 180 at all. The decision: **persist the angle per page alongside the run or
its artifact, outside `Document`**, so the result contract mmerp consumes does not change. The
page-image route applies that angle when it renders. Runs from before the change have no angle, are
rendered unrotated, and may show misaligned boxes on rotated pages. The viewer says so when the
angle is unknown and the parser was PaddleX.

## Goals

- **Fix what is broken today:**
  - the dead "administrative scope" checkbox (the server ignores it)
  - the result link that 401s whenever auth is on (it is a plain `<a href>` and sends no credential)
  - revoke has no confirmation
  - there is no sign-out
  - there is no refresh
  - errors and loading states are swallowed on run detail
- **Overview page:** described in decision 1.
- **Runs list across all tenants:**
  - columns for kind (OCR or extraction), tenant, and duration
  - filters for kind, status, and tenant
  - cursor paging as today
  - auto-refresh
- **Run detail:**
  - status and kind
  - a timeline: submitted, then started, then finished, with wait time and run time
  - tenant, parser, language, worker, attempts, error class, failed stage, and failure reason
    (decision 7)
  - a Cancel action backed by the existing `DELETE /v1/runs/{id}`
  - the result viewer from decision 3 for OCR runs
  - the delivery record, as today
- **Submit OCR:** upload a file, choose a tenant, parser, and language, submit it, and land on the
  new run's detail page to watch it.
- **Parsers:** the `GET /v1/parsers` data: availability, health, media types, and languages.
- **Tenants:** a list of known tenants. It is the entry point to that tenant's credentials and
  runs, and to erasing it.
- **Credentials, Callbacks, Erasure:** usable without typing a tenant by hand. Callbacks gain
  list, create, and delete.
- **Login, sign-out, and session expiry.**

## Non-goals

- A user-management screen, password reset by email, SSO, or roles. All of that is the CLI or
  later.
- Editing results or recording corrections from the console.
- Any metric beyond the Overview counts: no charts, no p50/p95 latency, no history graphs. This is
  the builder's call made in the conversation; the mockup showed "p50" and it is dropped.
- Grounding for extraction results (value-to-span highlighting). The page-and-box viewer is for OCR
  runs only. An extraction run keeps a working link to its result, fetched with credentials.

## Anti-goals: what would count as failure even if it works

- **A review platform.** ADR-0019 §2's five forbidden nouns stay forbidden: reviewer assignment,
  review queue, workload, review state beyond the run status set, and disposition. The Overview
  counts runs per *kind* and *status*, never per person. `check-console-boundary.mjs` and
  `tests/contract/test_no_review_platform.py` keep running. Route paths must avoid the words that
  test forbids. Note that **`queue` is one of them**, so the Overview route cannot be named
  `/queue`.
- **A tenant API key that can see another tenant's data.** The cross-tenant reads exist only behind
  a console session. A Bearer key, admin or not, must never reach them. This is the one place the
  upgrade could quietly break ADR-0014's isolation, and it needs tests that prove it.
- **Credentials or session tokens readable by JavaScript or stored by the page.** The existing
  no-browser-persistence guard stays. A server-set `HttpOnly` cookie is not page storage, and the
  guard should keep passing.
- **A console that only works with auth off.** That is exactly how the grounding viewer died
  (ADR-0020).

## Current state (verified 2026-10-20)

- **Frontend:**
  - React 19, TypeScript, and Vite 8 under `ui/`, with `base: "/console/"`.
  - The Astryx design system (`@astryxdesign/core` 0.5.0) is the only UI dependency.
  - There is no router: areas are switched with `useState` in `ui/src/console/components/Shell.tsx`.
  - There is one fetch site, `ui/src/transport.ts`. Every request goes through
    `ui/src/console/model/client.ts` and its `ALLOWED` allow-list.
  - Model tests run with `node --test`. Guard scripts live in `ui/scripts/`: model boundary, no
    browser persistence, console boundary nouns, and a11y.
- **Serving:** FastAPI mounts static files at `/console` (`src/docdoc/api/app.py` ~747–815).
  `_require_credential_everywhere_else` (app.py ~661–714) requires a Bearer key on every path
  except `/healthz`, `/readyz`, `/console`, and `/console/*`.
- **Auth:**
  - Tenant keys come from the `DOCDOC_API_KEYS_FILE` ring and the Postgres `credentials` table,
    hashed with SHA-256 (`runs/keys.py`, `api/auth.py`).
  - Auth is off when the keys file is unset.
  - `Principal(tenant_id, scopes)`. The only scope is `admin`, and admin routes answer 404 to anyone
    without it.
- **Storage:**
  - Runs, credentials, delivery, and corrections live in Postgres (`DOCDOC_RUN_DATABASE_URL`).
    Migrations are plain SQL in `src/docdoc/runs/migrations/`, applied by `docdoc migrate`.
  - Blobs and artifacts live on the filesystem or S3.
- **Run queries:**
  - Listing is per tenant only: `page_runs` and `runs_for` in `runs/postgres.py`.
  - The only cross-tenant count is `count_active(kind)`, which counts queued plus running.
  - Nothing groups by status, nothing lists tenants, and there is no stats route.
- **Workers:** `worker_id` is `hostname:pid` and is purely diagnostic. There is no worker registry.
- **OCR result:**
  - `GET /v1/runs/{id}/result` returns the `Document`.
  - Token geometry is `BBox` normalized to 0..1 with a top-left origin.
  - `Page` carries width, height, and rotation.
  - `Token.source_confidence` is optional, and a parser may give no confidence at all.
- **Original page images: none are served.**
  - `GET /v1/documents/{blob_id}` deliberately returns metadata, never bytes.
  - No code renders PDF pages. PaddleX sends the PDF bytes to the OCR host.
  - `pypdfium2` is available in the `ocr` extra. Pillow is dev-only.
- **Callbacks:** `POST /v1/callbacks` and `DELETE /v1/callbacks/{id}` exist. There is no list route,
  and callbacks are tenant-scoped.
- **CLI:** argparse in `src/docdoc/cli/`, with subcommands under `cli/commands/`. It has
  `credential issue|revoke|list` to follow as a pattern.
- **Constitution:** `.specify/memory/constitution.md`, under "Deferred technology", permits a
  console that lists and inspects runs, shows routing and delivery records, issues and revokes
  credentials, and triggers erasure, "plus one tenant-scoped read". Rendering results, cross-tenant
  views, and a login are outside that wording.

## Constraints

- **Governance first.** The constitution's console paragraph must be amended, and a new ADR must
  supersede the affected parts of ADR-0019 (§3 "no server-side concept", §4 "no login", and "renders
  no result") and partly ADR-0020 (page-image viewing returns, for OCR runs only, behind
  authentication). Both are written before code, in the style of the existing ADRs and amendment
  notes.
- **The console requires the run database.** Accounts and sessions live there. Without
  `DOCDOC_RUN_DATABASE_URL`, `/console` should say plainly that it is unavailable, in the spirit of
  today's 501 `ConsoleNotInstalled`.
- **The console requires login even when tenant-key auth is off.** The owner wants a login always.
- **No new frontend dependency for PDF rendering.** The server renders pages to images.
- **Passwords are hashed with a slow KDF from the standard library** (`hashlib.scrypt`) unless the
  builder finds a reason not to. No new Python dependency just for this.
- **Session cookie:** `HttpOnly`, `SameSite=Strict`, and `Secure` when served over HTTPS. The
  session expires when idle. The 15 minutes the console uses today is the default, and the builder
  makes it configurable.
- **State-changing console routes must not be forgeable cross-site.** `SameSite=Strict` plus
  requiring a JSON body or a custom header is enough.
- **Login is rate-limited or backs off** after repeated failures per username.
- **Destructive actions keep their confirmations, and revoke gains one.** Erasure keeps retyping the
  id. Every destructive console action is logged with the console username.
- **Visible text is English, matching the existing console.**

## Shape of the server work (direction; details are the builder's)

1. **Console accounts.**
   - A migration adds a users table (username, password hash, created, disabled) and a sessions
     table, or the builder makes another session design that meets the constraints.
   - CLI: `docdoc console-user add|passwd|disable|list`. The exact verbs are the builder's.
2. **Console session auth.**
   - Login and logout routes.
   - A dependency that resolves a session cookie into a console principal.
   - The middleware has to admit console-API paths on a session instead of a Bearer key, without
     widening `_is_console_asset`'s exemption into an unauthenticated API.
   - **Caution:** if console API routes live under `/console/...`, the current exemption would let
     them through unauthenticated. The builder chooses the prefix, for example `/v1/console/...`,
     and proves with tests that a Bearer key cannot reach it and no credential cannot reach it.
3. **Cross-tenant reads behind the console session:**
   - Overview counts by kind and status, active now plus terminal in the last hour.
   - Run list across tenants with kind, status, and tenant filters, plus `tenant_id` on each row.
   - Run detail by id regardless of tenant.
   - Tenant list: the distinct tenant ids from `credentials` ∪ `runs`.
   - Callbacks list.
4. **Console versions of the actions** that today need a tenant or admin key: cancel a run, issue
   and revoke a credential, erase a tenant or document, create and delete a callback, and submit
   OCR. Each one takes the target tenant explicitly. They should reuse the existing route logic
   rather than fork it. Issuing an **admin-scoped** key stays CLI-only, as today.
5. **Page images.**
   - A route returns page *n* of an OCR run's source document as an image.
   - PDF pages are rendered with `pypdfium2`.
   - Image documents are returned as they are, or normalized.
   - It is served only to a console session.
   - The image's pixel size and orientation must match how the normalized boxes are interpreted,
     so the overlay is computed as bbox × rendered size. Pages rotated by PaddleX are the case to
     test.
   - If PNG encoding needs Pillow, the builder decides how it becomes a runtime dependency of the
     right extra.
6. **The frontend fetches everything with credentials.** That includes images, which are fetched,
   turned into blob URLs, and shown. The result link 401 is fixed the same way.

## Frontend shape

- **Sidebar** in this order: Overview, Runs, Submit OCR, Parsers. Under an admin divider: Tenants,
  Credentials, Callbacks, Erasure. Sign out sits at the bottom with the logged-in username. Everyone
  logged in sees the admin areas; the divider only groups them.
- **Overview and Runs poll** every few seconds and pause while the tab is hidden.
- **Run detail** follows the mockup:
  - a header with the badge, kind, and Cancel
  - the timeline
  - a metadata grid
  - for OCR runs, the split viewer: page image with boxes on the left, lines on the right, a
    two-way hover highlight, low-confidence lines tinted, a page pager, and Download JSON
  - for extraction runs, a credentialed "Result" action
- **The credential-paste screen is replaced by a login form.**
- **A router is the builder's call.** Deep links to a run (`/console/runs/<id>`) would be useful.
  If added, it must work with `StaticFiles(html=True)`.
- **The mockups** are in `.superpowers/brainstorm/*/content/`: `layout.html` (option A chosen) and
  `run-detail.html` (option B chosen). They show structure, not visual design. The finished screens
  use the Astryx components already in use.

## Calls made by the designer (not the owner) — check these

- Existing ADR-0019 guards stay. The new ADR re-scopes them rather than deleting them.
- Overview "last hour" window and the 5-second poll interval.
- Overview p50 latency dropped.
- Console sessions stored in Postgres, with a cookie, idle expiry, and scrypt.
- Console API is a separate route family behind the session, not an extension of Bearer auth.
- Tenant list derived from `credentials` ∪ `runs`. There is no tenants table.
- Submit OCR asks which tenant to submit as, because a console user has no tenant of their own.
- Issuing admin-scoped keys stays CLI-only.
- Destructive actions are recorded in the server log with the console username. There is no audit
  table.
- The page-and-box viewer covers OCR runs only.
- **Timeline data.** `runs` has only `created_at` and `updated_at`. Add `started_at` (set on the
  most recent claim) and `finished_at` (set on reaching a terminal status) in a migration. Wait
  time runs from `created_at` to the latest `started_at`, so retries are folded into the wait.
  Runs older than the migration show "—". The Overview's "last hour" counts come from
  `finished_at`.
- **One box per token, not per line.** `Document` stores no lines, and `kernel/document.py`
  refuses to merge boxes. PaddleX tokens are whole lines, while `pdf_text` and `gcv` tokens are
  words. The viewer shows whatever the parser produced.
- **Low-confidence threshold.** One deployment-wide setting. When it is unset, nothing is tinted.
  `source_confidence` is shown on hover whenever it exists. The threshold is not per parser, which
  means it cannot be compared across parsers whose scales differ. Add that if it bites.

## Left to the builder

- Route names and payloads, table schemas, CLI verbs, the session-store details, and the polling
  mechanics.
- Whether to add a router, and how to split `Shell.tsx` and the model modules.
- How to keep the model and component boundary and the `ALLOWED` allow-list discipline while
  adding the new requests.
- Delivery order. Suggested: governance (ADR and amendment), then fixes, then accounts and session,
  then the cross-tenant reads with Overview and Runs, then run detail with the page viewer, then
  Submit OCR, Parsers, Tenants, and Callbacks.
