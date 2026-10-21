# How the operations console works

A browser interface for the person who **runs** a deployment: what ran in every tenant, which
credentials exist, and what to erase. It is not a review platform and holds no per-reviewer state — no assignment, no
queue, no workload, no review state, no disposition. That boundary is ADR-0019's and it is enforced
by a build check rather than by review (`npm run lint:boundaries`).

Install it with `pip install 'docdoc[api,ui]'` and serve it the way you already serve the API:

```bash
uvicorn --factory docdoc.api.app:create_app --host 127.0.0.1 --port 8000
# then open http://127.0.0.1:8000/console/
```

The console needs the run database (`DOCDOC_RUN_DATABASE_URL`), because accounts and sessions live
there ([ADR-0023](../adr/0023-console-login-and-cross-tenant-operations.md)). Without it every
console route answers `503 ConsoleUnavailable` and the page says so instead of offering a form.

There was a second browser surface here — Milestone 8's grounding viewer, at `/ui` — until
[ADR-0020](../adr/0020-retire-the-grounding-viewer.md) removed it. `/ui` is now a `404`.

## The six things this page has to tell you

Most of them are the kind of sentence that costs nothing to leave out and is discovered missing only
by the person it would have warned.

1. **The rendered interface carries no automated test.** The decisions it makes are tested; the
   screen is not. See "What is tested" below, which says exactly where the line falls and what can
   break without anything going red.
2. **You sign in with a console account, and accounts exist only on the command line.** There is no
   sign-up screen and no account screen: `docdoc console-user add|passwd|disable|list` is the only
   writer (ADR-0023 §1). A reload keeps you signed in, because the session is a cookie the server
   holds the record of.
3. **An idle session ends on the server.** After `DOCDOC_CONSOLE_IDLE_SECONDS` (default `900`)
   without a request, the server refuses the session; the page keeps no clock of its own and learns
   it from the next request, then returns to the form with "Your session ended. Sign in again."
4. **No accessibility conformance level is claimed.** What is guaranteed is narrower and actually
   kept: every control carries an accessible name and is operable by keyboard alone — the erasure
   confirmation and the sign-in form included — and state changes reach assistive technology
   rather than only the eye. A standard nothing here verifies would be a claim, not a guarantee.
5. **Behind a TLS-terminating proxy, run `uvicorn --proxy-headers`.** The session cookie carries
   `Secure` only when the request the application sees is `https`; behind a proxy that is true only
   if uvicorn trusts the proxy's `X-Forwarded-Proto` (`--proxy-headers`, with
   `--forwarded-allow-ips` naming the proxy). Otherwise the cookie is sent without `Secure`.
6. **A succeeded run's result can be downloaded.** ADR-0023 §4 lets the console render results; for
   now it offers **Download result**, which saves the JSON the deployment serves for that run — an
   OCR run's `Document` or an extraction run's result — as `<run_id>.json`.

## Getting in

Create an account first, on a machine that can reach the run database:

```bash
docdoc console-user add alice                       # asks for the password twice
printf '%s\n' "$PASSWORD" | docdoc console-user add alice --password-stdin   # for scripts
```

Usernames are `[a-z0-9_.-]`, up to 64 characters; passwords are at least 12. Then open `/console/`
and sign in. The shell itself loads without a session, so a top-level navigation can always reach
the form; every request the page then makes goes to `/v1/console`.

**The session** is a cookie named `docdoc_console` — `HttpOnly`, `SameSite=Strict`,
`Path=/v1/console`, and `Secure` over `https`. The server stores only a hash of it. Every request
under `/v1/console` that is not a `GET` or `HEAD` must also carry `X-Docdoc-Console: 1`, or it is
refused with `403`; the page sends it on every request.

**A refused sign-in** says so in one sentence, the same for a wrong username, a wrong password, a
locked account, and a disabled one. Five failures in a row lock an account for five minutes.

**Sign out**, at the bottom of the sidebar, ends the session on the server, not only in the page.

## What it does

The page is a left sidebar — **Overview**, **Runs**, **Submit OCR**, then under "Admin" **Credentials** and
**Erasure**, with the username and **Sign out** at the bottom — and the chosen area on the right.
It opens on the Overview.

| Area | What it offers | Where the write goes |
|---|---|---|
| **Overview** | Four cards — Queued, Running, Failed (last hour), Done (last hour) — each a total split "OCR n · Extraction n"; Failed turns red when it is above zero. Then **Recent failures** (kind, tenant, reason, finished) and **Running now** (kind, tenant, parser, run time), ten each; a run id opens that run under Runs. | nothing — reads only |
| **Runs** | Every tenant's runs, newest first; filterable by tenant, kind (OCR or extraction) and the five states, paged by cursor, and refreshable. Columns: state, kind (an OCR run names its parser), tenant, submitted, duration, run. One run's timeline (submitted → started, with the wait → finished, with the run time), tenant, parser and language for OCR, worker, attempts, error class, failure reason, routing outcome where a policy is configured, and delivery record; **Download result** for a succeeded run, and **Cancel run** — confirmed inline first — for a queued or running one. A failed run names the stage it failed at and the error's class. | `DELETE /v1/console/runs/{id}` (cancel) |
| **Credentials** | Issue, list (per tenant, on **Show**), revoke — each revocation confirmed in its row first. The issued key is shown exactly once. Console-issued keys are tenant keys; the administrative scope is minted only by `docdoc credential issue --admin`. | `POST`/`DELETE /v1/console/credentials` |
| **Erasure** | One tenant, or one document within a named tenant, per confirmation. | `DELETE /v1/console/tenants/{id}`, `DELETE /v1/console/tenants/{id}/documents/{blob}` |

**The route family.** The console speaks only `/v1/console/*`, which answers only to the session
cookie; the rest of `/v1` answers only to a Bearer key, and neither opens the other's doors
(ADR-0023 §3):

| Route | What it does |
|---|---|
| `POST`/`GET`/`DELETE /v1/console/session` | sign in, who am I, sign out |
| `GET /v1/console/overview` | the overview's counts, recent failures, and running runs |
| `GET /v1/console/runs` | every tenant's runs, or one with `?tenant=`; `?kind=ocr\|extraction` narrows by kind |
| `GET /v1/console/runs/{id}`, `…/delivery`, `…/result` | one run, its delivery record, its result |
| `DELETE /v1/console/runs/{id}` | cancel a queued or running run |
| `GET`/`POST /v1/console/credentials`, `DELETE …/{id}` | list (`?tenant=`), issue, revoke |
| `DELETE /v1/console/tenants/{id}`, `…/documents/{blob}` | erase a tenant, or one document |

Every signed-in operator sees every area; there is one level of access (ADR-0023 §2). Every
destructive action — a cancellation included — is logged with `actor="console:<username>"`.

**The Overview and the run list refresh themselves every 5 seconds** while the tab is visible.
A hidden tab stops polling, and becoming visible again refreshes at once rather than waiting out the
interval. The Overview shows when it last refreshed; a refresh that fails keeps the last good answer
on screen and shows the failure above it. The run list refreshes only on its first page: paging on
stops it, so rows do not move under the reader, and returning to page one resumes it.

**The counts' window.** Queued and Running count every such run, however old. Failed and Done count
only runs that reached that state in the last 60 minutes; Done is succeeded runs only, and a
cancelled run lands in no card.

**A failed run says why.** The stage it failed at and the typed error's class name, as the
deployment recorded them — `extract` / `ModelProviderError` — and, where the error carries one, its
reason in fixed copy ("The OCR host timed out" for `timeout`). Never a message: the route sends none,
because a provider's text may quote the document it choked on. A run that failed before any stage ran
says that instead of naming one, which is a real distinction — an unresolvable schema fails a run
with nothing having executed.

**Result view.** A queued or running run's detail reads the run again every 5 seconds, with the same
hidden-tab pause as the Overview, until it settles. A run that settles succeeded, if it is an OCR run,
has its result read once and shown under **Download result**. Each page's image is on the left with a
thin box drawn over every recognised line. The lines are on the right, each with its confidence
("—" where the OCR host gave none). Hovering a line fills its box, and hovering a box highlights its
line and scrolls the line into view. **Previous** and **Next** step through the pages, and each page's
image is fetched the first time that page is shown. A turned page is shown upright, with its boxes
lined up, when the OCR host reported the turn. Otherwise, if the OCR host's own page dimensions disagree
with the image's orientation, a warning says the page was turned and the boxes may not line up. An
image that cannot be fetched is replaced by an error, and its text still shows. A failed run requests
no result, and an extraction run keeps **Download result** only.

**Submit OCR** sends one document for OCR on a tenant's behalf, through
`POST /v1/console/tenants/{id}/ocr` — the same path, caps and validation as `POST /v1/ocr` with
that tenant's key. Pick a tenant (from `GET /v1/console/tenants`), a parser (Automatic, or one from
`GET /v1/console/parsers`; an unavailable parser is listed but cannot be chosen, one failing its
health check is marked "not responding"), a language (the parser's default, or one of that parser's
languages; changing the parser resets it), and the document. A refusal is shown in the form; an
accepted document opens that run under Runs. If the tenant or parser list fails to load, the form
says so and stays usable with whatever did load.

**An erasure requires the target typed back**, character for character, and is unavailable until it
matches. There is no bulk erasure and no multi-select: one named target per confirmation. The counts
reported afterwards are the deployment's own, unsummed and unrounded.

## Building it

Two builds, and they produce different things.

**The application**, from a checkout — this is what you run while developing, and what
`docdoc.api.ui` prefers whenever a checkout has been built:

```bash
cd ui && npm ci && npm run build     # writes ui/dist, which is gitignored
```

**Which build you get, when more than one exists.** Three places can hold assets, and they are
preferred in this order:

| Order | Root | Used when |
|---|---|---|
| 1 | `DOCDOC_CONSOLE_ROOT` | you named a directory, so you meant it |
| 2 | the checkout's `ui/dist` | you are running from a source tree that has been built |
| 3 | the installed `docdoc-ui` distribution | everything else, which is every real deployment |

**A checkout's own build wins over an installed copy**, and it did not always. On any machine that
has run `packaging/docdoc-ui/build.sh` or synced the `ui` extra, both exist — and the installed one
used to win, silently, so `npm run build` appeared to change nothing. A months-old bundle was served,
its page looked wrong in ways that matched no source file, and it was read as evidence about current
code before anyone thought to check which assets were on the wire.

**If the page does not match the source you are editing, ask which root won:**

```bash
python -c "from docdoc.api.ui import chosen_assets; print(chosen_assets())"
# ("the checkout's ui/dist", PosixPath('/…/ui/dist'))
```

That answers without needing a running server or any logging setup, which is why it is the one
written down here. The application also emits the same fact once at startup —

```
{"event": "console.assets", "source": "the checkout's ui/dist", "path": "/…/ui/dist"}
```

— on the `docdoc.api` logger at `INFO`, and **you will not see it unless your application configures
logging**. `uvicorn` configures its own `uvicorn.*` loggers and leaves the root logger without a
handler, so docdoc's structured events fall to `logging.lastResort`, which drops anything below
`WARNING`. That is true of every structured event docdoc emits, not only this one; making a library
visible by default is the application's decision and not one this package takes on its behalf.

**If there is no build at all, `/console` answers `501`** naming what is missing and the one command
that fixes it — a missing build and a missing distribution get different sentences, because telling a
developer to `pip install` what they have the source for is unhelpful accuracy. It never serves a
blank page, and an empty `ui/dist` left behind by a failed build is not mistaken for a build: the
entry point has to be there.

**The distribution**, which is what `pip install 'docdoc[ui]'` actually delivers:

```bash
./packaging/docdoc-ui/build.sh       # builds the console, copies ui/dist, produces the wheel
```

The order inside that script is the point: assets are copied in immediately before the wheel is built
and are never committed, so a checkout holds no build output at any moment. That is why `git status`
is a meaningful check in CI rather than a formality, and why the assets live in a second distribution
at all — a Python extra can add a dependency but cannot add files to a wheel everyone already
installs, and the base install must acquire no static asset.

## Nothing comes to rest in the browser

No `localStorage`, no `sessionStorage`, no IndexedDB, no Cache API, and no cookie the page writes or
reads. Not for a run, not for a filter you set. The one cookie is the server's `HttpOnly` session,
which the page cannot see. `scripts/check-no-persistence.mjs` fails the build over all of
`ui/src/` if any of them appears, which is what makes "zero recoverable bytes after a session" a
check rather than an intention.

A reload keeps the session, because the server holds it; it loses any filter you set.

## The five nouns, and what they cost

The console may not hold, display, or let anyone set: **reviewer assignment**, **a review queue**,
**workload**, **a review state**, **a disposition**. Fifteen words may not appear in any route path:
`assign`, `assignment`, `reviewer`, `reviewers`, `queue`, `worklist`, `work-list`, `inbox`, `task`,
`tasks`, `approve`, `approval`, `reject`, `escalate`.

This costs something real, and the cost is recorded here rather than discovered later: an operations
console genuinely wants to show how much work is waiting for the workers, and the obvious name for
that contains `queue`. It gets a different name — `backlog` — or it is not built.

Widening the check to "one of these words **plus** a noun about a person" was rejected in ADR-0019
§3. A guard that has started reasoning is one a future route can argue its way past; a word list
either matches or does not, which is the only property that makes it still true in two years.

## What is tested, and what is not

Everything the console *decides* lives in `ui/src/console/model/` as pure functions, and those are
tested with `node --test`: which requests may be constructed, what a failure means, how paging moves,
what an empty list says, what a signed-in session holds, and how a delivery record reads.

The rendering layer is not tested at all. That was a deliberate choice, and it costs something
specific: a control that is present in the model and hidden by a layout, a status colour rendered
without its word, a confirmation field wired to the wrong target, or a banner that never reaches the
screen — none of these would go red.

What contains the gap is that the rendering layer decides nothing, and that is machine-checked rather
than hoped for. `npm run lint:boundaries` runs four checks and fails if any finds something:

| Check | Fails when |
|---|---|
| `check-model-boundary.mjs` | the model imports React or a component; a component calls `fetch` |
| `check-no-persistence.mjs` | anything under `src/` touches browser storage or a cookie |
| `check-console-boundary.mjs` | one of the five nouns appears (its result-content rules were retired by ADR-0023 §6) |
| `check-console-a11y.mjs` | a control has no accessible name; a status message has no live region |

A renderer with no decisions in it has little room to be wrong on its own. If that gap is later
judged too large, the fix is additive — the view model is already the seam a browser test would
attach to.
