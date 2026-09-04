# How the viewer works

A browser interface that shows which page and which rectangle each extracted value came from, and says
plainly when there is none. It is **read-only**: it displays results and never edits them.

Install it with `pip install 'docdoc[api,ui]'` and serve it the way you already serve the API:

```bash
uvicorn --factory docdoc.api.app:create_app --host 127.0.0.1 --port 8000
# then open http://127.0.0.1:8000/ui/
```

## The seven things this page has to tell you

Most of them are the kind of sentence that costs nothing to leave out and is discovered missing only
by the person it would have warned.

1. **The rendered interface carries no automated test.** The decisions it makes are tested; the screen
   is not. See "What is tested" below, which says exactly where the line falls and what can break
   without anything going red.
2. **A run continues after you close the page.** There is no cancel, because closing the browser stops
   the waiting and not the work — the extraction runs to completion and the provider is paid either
   way. If you put a proxy in front of this, allow a request duration at least as long as your slowest
   extraction, or the proxy will terminate runs you have already paid for.

   When that happens the viewer says so accurately: it reports that **the connection was lost and the
   run was not**, that the extraction is still going and its cost is already incurred, and that a
   storeless run keeps no job identity — so that particular answer cannot be fetched afterwards and
   running it again is a second extraction at a second cost. It used to call this "The run failed",
   which was the opposite of the two facts an operator needs, and is why this page previously warned
   that a proxy timeout "will look like a viewer bug". It no longer looks like one.
3. **Pages are shown selectively.** The viewer renders the pages carrying located values, not the
   whole document — a deployment accepts documents of up to 1000 pages by default, and rendering all
   of them in a browser fails. Every other page is reachable, and the interface says on screen how
   many of how many it is showing.
4. **No accessibility conformance level is claimed.** What is guaranteed is narrower and actually
   kept: every fact the overlay conveys is also in the value list, the list works without a pointing
   device, and no distinction — grounding status, verdict, geometry state — is carried by colour
   alone. A standard nothing here verifies would be a claim, not a guarantee.
5. **By default the interface is unauthenticated, and every visitor spends your provider budget.**
   That second half is the part you cannot infer from the first. Anyone who can load the URL can
   submit documents through your model provider. Milestone 9 added authentication and left it off, so
   this stays true of every deployment that has not set `DOCDOC_API_KEYS_FILE`.

   **Turning it on does not secure the viewer — it removes it.** `/ui` then requires a credential
   like everything except the two health probes, and a browser cannot send a bearer token, so the
   interface becomes unavailable rather than protected. That is the honest outcome: it never worked
   under authentication, and it now fails at the door instead of after the page has rendered. A
   viewer that works with authentication needs a session mechanism, and there is deliberately none.
6. **It belongs on a trusted network.** docdoc ships no `serve` command, so it cannot choose a safer
   bind address on your behalf; that is the operator's decision and this is the note saying so.
7. **A deployment with no store configured now serves extractions it used to refuse.** `POST
   /v1/extract` needs no store (ADR-0012). If you read the old refusal as a closed door, it has
   opened.

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
| 1 | `DOCDOC_UI_ROOT` | you named a directory, so you meant it |
| 2 | the checkout's `ui/dist` | you are running from a source tree that has been built |
| 3 | the installed `docdoc-ui` distribution | everything else, which is every real deployment |

**A checkout's own build wins over an installed copy**, and it did not always. On any machine that has
run `packaging/docdoc-ui/build.sh` or synced the `ui` extra, both exist — and the installed one used to
win, silently, so `npm run build` appeared to change nothing. A months-old bundle was served, its page
looked wrong in ways that matched no source file, and it was read as evidence about current code
before anyone thought to check which assets were on the wire.

**If the page does not match the source you are editing, ask which root won:**

```bash
python -c "from docdoc.api.ui import chosen_assets; print(chosen_assets())"
# ("the checkout's ui/dist", PosixPath('/…/ui/dist'))
```

That answers without needing a running server or any logging setup, which is why it is the one written
down here. The application also emits the same fact once at startup —

```
{"event": "ui.assets", "source": "the checkout's ui/dist", "path": "/…/ui/dist"}
```

— on the `docdoc.api` logger at `INFO`, and **you will not see it unless your application configures
logging**. `uvicorn` configures its own `uvicorn.*` loggers and leaves the root logger without a
handler, so docdoc's structured events fall to `logging.lastResort`, which drops anything below
`WARNING`. That is true of every structured event docdoc emits, not only this one; making a library
visible by default is the application's decision and not one this package takes on its behalf.

**The distribution**, which is what `pip install 'docdoc[ui]'` actually delivers:

```bash
./packaging/docdoc-ui/build.sh       # builds the app, copies ui/dist, produces the wheel
```

The order inside that script is the point: assets are copied in immediately before the wheel is built
and are never committed, so a checkout holds no build output at any moment. That is why `git status`
is a meaningful check in CI rather than a formality, and why the assets live in a second distribution
at all — a Python extra can add a dependency but cannot add files to a wheel everyone already
installs, and the base install must acquire no static asset.

## The document never reaches the server's disk

You pick a file locally. The browser renders it with pdf.js, and posts the bytes to `POST /v1/extract`,
which runs the pipeline and **persists nothing** — not a blob, not an artifact, not a temporary file,
and not even when a store *is* configured, because whether a run persists is a property of the endpoint
you called rather than of how the deployment is set up.

`GET /v1/documents/{blob_id}` still never returns bytes. Nothing was added that hands a document back.

The accepted consequence: reloading the page loses the view, and you pick the file again. A result
cannot be reopened later from its identity, because a storeless run has no identity to reopen — there
is no terminal artifact, and ADR-0003 makes the job id *be* the terminal artifact id.

## Three states that are not two

The engine keeps three facts apart about where a value is, and the viewer keeps them apart too:

| State | Means |
|---|---|
| **Located** | Boxes, on named pages. |
| **No page geometry** | Grounded in the text; this parser supplied no geometry at all. |
| **Covers no tokens** | Geometry exists, and this range covers none of it. |

None of the last two is "not found", and neither is drawn. A fourth situation — the model reported the
field **absent** — has no grounding outcome at all and is not "ungrounded": it is listed as absent
rather than omitted, because a row missing from the screen is indistinguishable from a field the schema
never had.

A fifth situation exists only in a **partial** result: the run failed at the grounding stage, so no
stage ever reached a conclusion about the value. It is listed as *grounding did not run*, which is
neither "absent" nor "not located" — both of those report what a stage decided, and here nothing
decided anything. Three defects in this milestone came from collapsing these; the distinctions are
kept by one function so that a row cannot describe itself two ways at once.

Scores never cross tiers. An exact score is `1.0` by definition and a fuzzy score is a measured
similarity, so they are never placed on a shared scale, ranked against each other, or blended
(ADR-0004). Where a score appears, its tier appears with it.

## What it can open, and what happens when it cannot

| You pick | You get |
|---|---|
| **PDF** | Every page the result names, rendered, with the rectangles drawn over them. |
| **PNG or JPEG** | One page, rendered, with rectangles. Such a document has exactly one page and needs no splitting — and the `gcv` parser accepts these two and no PDF at all, so a deployment configured with it can process nothing else. |
| **Anything else** | **No picture, and every value still listed.** |

That last row is the interesting one, and it is a requirement rather than a convenience. The list is
authoritative and the overlay is an aid, so a document the browser cannot draw must not lose its facts
as well as its rectangles — it loses the picture and nothing else.

The viewer does not decide whether such a document is *acceptable*. It says only that it cannot draw
the type; whether the deployment extracts it is the deployment's answer, given with a typed reason
(`image/tiff` is a real example: docdoc detects it precisely so it can refuse it as an unsupported
type). Putting a copy of the deployment's allowlist in the browser would be two copies of one rule,
which is how the two come to disagree.

## What a run that failed part way still shows

An extraction that fails at one stage does not throw away the stages before it. The response carries
what they produced, and a failed run has **no job identity to fetch afterwards**, so that response is
the only place those results will ever appear.

The viewer therefore names the failing stage *and* lists the values that survived it, through the same
code that lists a complete run's — same rows, same statuses, same labels. A partial result is not given
a presentation of its own, because two presentations are how the two come to disagree about what a
grounding status means.

Where a stage produced nothing, the key is simply absent and the viewer says nothing about it. An
omitted stage and a stage that ran and found nothing are different facts, and the per-stage outcome
list is what tells them apart.

## Why no coordinate is transformed

Boxes arrive normalized to `0..1` with a top-left origin — already the coordinate system of a CSS box —
so the overlay multiplies by 100 and does nothing else.

In particular it **never reads `Page.rotation`**, and that is the single most important line in this
document for anyone changing the viewer. The parsers resolve rotation *before* normalizing:
`pdf_text.py` maps every word box through `page.rotation_matrix` and normalizes against the displayed
page rect, and the other two adapters record `rotation = 0` because their services already report
displayed coordinates. A box that reaches the browser is already oriented the way the page is shown.

Reading `Page.rotation` and rotating the overlay to match is the plausible wrong implementation. It
double-rotates, and it fails *only* on rotated pages — so it looks correct everywhere you are likely to
test it, and no automated test in this repository would catch it.

## What is tested, and what is not

Everything the viewer *decides* lives in `ui/src/model/` as pure functions, and those are tested with
`node --test`: which boxes belong to a value and where, which values are listed and in what state, what
a failure says, which pages render, what a new document clears, and which requests may be constructed.

The rendering layer is not tested at all. That was a deliberate choice, recorded in the spec's first
clarification, and it costs something specific: a box the model placed correctly and the renderer draws
at the wrong scale, a rotated page whose boxes land on the wrong content, a value listed in the model
and hidden by a layout, three geometry states rendered so similarly that the distinction is invisible,
or a stale rectangle surviving a document change in the renderer after the model cleared it — none of
these would go red.

What contains the gap is that the rendering layer decides nothing, and that is machine-checked rather
than hoped for: `npm run lint:boundaries` fails if `ui/src/model/` imports React, the renderer, or a
component, and fails if a component contains an editable control. A renderer with no decisions in it has
little room to be wrong on its own.

If that gap is later judged too large, the fix is additive — the view model is already the seam a
browser test would attach to.
