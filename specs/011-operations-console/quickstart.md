# Quickstart: Validating the Operations Console

**Feature**: `011-operations-console` | **Date**: 2026-09-11 |
**Last run end to end**: 2026-09-11 (T072), against the four-container composition. That pass found
**six** things this document got wrong: the image carries no browser assets so both surfaces answered
`501`; `docdoc credential issue` takes `--admin` and not `--scope`; `GET /v1/admin/credentials`
requires a `tenant_id` and answers `422` without one — which is a defect the **console** had too, and
it hid the administrative areas from administrators; an empty key file is refused at startup so
authentication cannot be enabled with one; and the erasure response carries different field names
than assumed. All are corrected below. Scenarios 1 and 7 have browser halves that were **not** run.

Every scenario maps to a success criterion in [spec.md](./spec.md). The criterion is what is being
checked; the commands are one way to check it. Routes and their shapes are in
[contracts/console-http-api.md](./contracts/console-http-api.md); what the build guards assert is in
[contracts/console-guards.md](./contracts/console-guards.md).

**Scenario 0 comes first because it is the claim the milestone makes first**: a deployment that does
not serve the console sees nothing change.

## Prerequisites

The same four containers Milestone 9 brought up. **No fifth** (SC-014):

```bash
uv sync --all-extras
npm --prefix ui ci

DOCDOC_PG_PORT=55432 \
DOCDOC_EXTRAS=api,postgres,s3,pdf \
  docker compose -f packaging/docker/compose.yml up -d --build

docker compose -f packaging/docker/compose.yml exec api docdoc migrate
docker compose -f packaging/docker/compose.yml exec api docdoc migrate --check   # exits 0
```

**`docdoc migrate --check` must report nothing to apply for this milestone too.** Milestone 11 adds
**zero** migrations; if this command wants to apply one, something has been built that the plan says
should not exist.

Build both browser entries. **The image carries neither**, and never will — the assets live in the
separate `docdoc-ui` distribution, which the default extras omit. `compose.yml` mounts the host's
builds read-only at `/clients/viewer` and `/clients/console` and points `DOCDOC_UI_ROOT` and
`DOCDOC_CONSOLE_ROOT` at them. Skip this and both surfaces answer `501` naming what is missing:

```bash
npm --prefix ui run build            # viewer      -> ui/dist
npm --prefix ui run build:console    # console     -> ui/dist-console
docker compose -f packaging/docker/compose.yml restart api   # if the API was already up
```

---

## Scenario 0 — a deployment that does not serve it is unchanged (SC-013, SC-002)

**The recipe changed, and it is worth saying why.** This scenario used to read `rm -rf
ui/dist-console` and expect a `501`. That stopped working two tasks after it was written: T072 added
a read-only bind mount of that directory into the api container, so deleting it on the host leaves
the mount pointing at a dead inode and the API serving whatever it already resolved. Unsetting the
setting is the honest way to produce "this deployment does not serve the console", and it needs no
restart of anything else:

```bash
DOCDOC_PG_PORT=55432 DOCDOC_EXTRAS=api,postgres,s3,pdf \
  DOCDOC_CONSOLE_ROOT=/nonexistent \
  docker compose -f packaging/docker/compose.yml up -d api

curl -so /dev/null -w "%{http_code}\n" localhost:8000/console   # 501
curl -s localhost:8000/console                                   # names what is missing and what fixes it
curl -s localhost:8000/v1/schemas | head -c 200                  # unchanged
uv run pytest -q                                                 # the suite Milestone 10 left green
```

Verified 2026-09-11. The `501` body reads *"DOCDOC_CONSOLE_ROOT is set to /nonexistent and there is
no console.html there"* — the setting a deployment named, not a guess about which of three roots it
meant.

Put it back by bringing `api` up without the override:

```bash
DOCDOC_PG_PORT=55432 DOCDOC_EXTRAS=api,postgres,s3,pdf \
  docker compose -f packaging/docker/compose.yml up -d api
curl -so /dev/null -w "%{http_code}\n" localhost:8000/console/   # 200
```

**The browser's half of the same failure** (T082) is a different check, because the `501` above only
covers assets the deployment does not have. With the assets served but the **bundle** unreachable —
a proxy mangling it, a stale cache, JavaScript disabled — the shell would otherwise render blank:

```bash
curl -s localhost:8000/console/ | grep -c "could not start"   # 1
curl -s localhost:8000/console/ | grep -c "noscript"          # present
```

Both verified 2026-09-11. The sentence lives inside `#root` and React replaces it only on a
successful mount, so it is on screen from the first byte and gone the moment the console runs. A
blank page is what `specs/008` FR-037 forbids on the server side; this is the client side of it.

Then the diff assertion, which is the one that cannot be argued with:

```bash
uv run pytest tests/contract/test_protected_layers_untouched.py -q
```

**Expected**: zero files under `kernel/`, `ingest/`, `extraction/`, `grounding/`, `validation/`.

---

## Scenario 1 — the shell loads without a credential, and the data does not (SC-005, research R1)

**Enabling authentication needs a non-empty key file.** An empty one is refused while the API starts,
with the reason: *"an empty key file enables authentication that nothing can pass"*. The table-issued
credentials of Milestone 10 do not turn authentication on by themselves — only the file does
(`specs/010` FR-088) — so a bootstrap entry has to exist even when every working key comes from the
table:

```bash
uv run python -c "
import json
from docdoc.runs.principal import digest_of
json.dump({'keys': [{'sha256': digest_of('bootstrap-key'), 'tenant_id': 'acme'}]},
          open('packaging/docker/secrets/keys.json', 'w'))"

DOCDOC_PG_PORT=55432 DOCDOC_EXTRAS=api,postgres,s3,pdf \
  DOCDOC_API_KEYS_FILE=/secrets/keys.json \
  docker compose -f packaging/docker/compose.yml up -d api
```

Then:

```bash
curl -so /dev/null -w "%{http_code}\n" localhost:8000/console/      # 200 — the shell is not gated
curl -so /dev/null -w "%{http_code}\n" localhost:8000/v1/runs       # 401 — the data is
curl -so /dev/null -w "%{http_code}\n" localhost:8000/ui/           # 401 — the viewer is unchanged
curl -so /dev/null -w "%{http_code}\n" localhost:8000/consoleroom   # 401 — the prefix is /console/
```

All four verified on 2026-09-11. The last one matters: the cheap implementation of the exemption is
`startswith("/console")`, which would also open a route named `/consoleroom` years from now.

**Expected**: the shell loads, every `/v1` call without a key is refused, and the viewer's posture is
exactly what Milestone 8 left. If `/console/` answers `401`, the console cannot be used on any
authenticated deployment — that is the failure research R1 exists to prevent.

Then in a browser at `http://localhost:8000/console/`: paste a key, confirm a run list appears,
reload, confirm the console asks for the key again.

**Then the check that matters** — with the tab open, in the browser console:

```js
Object.keys(localStorage).length + Object.keys(sessionStorage).length   // 0
document.cookie                                                          // ""
```

**Expected**: `0` and empty (SC-004). Also confirm the key never appears in the address bar, in
`history`, or in the server's request log (SC-005).

---

## Scenario 2 — paging returns every run exactly once (SC-009, FR-018)

Submit more runs than one page holds, then walk the cursor:

```bash
curl -s -H "Authorization: Bearer $KEY" 'localhost:8000/v1/runs?limit=2' | jq -r '.runs[].run_id'
# take .next_cursor, repeat until it is null, collecting ids
```

**Expected**: the collected ids are the submitted ids, with no duplicate and no omission. Submit a
new run **between two pages** and repeat: the new run may be absent (it sorts newest-first, before
the cursor), and no previously seen run may repeat. That second half is what an `OFFSET` page would
fail.

Cursor refusals:

```bash
curl -si -H "Authorization: Bearer $KEY" 'localhost:8000/v1/runs?cursor=not-base64' | head -1   # 400
curl -si -H "Authorization: Bearer $KEY" "localhost:8000/v1/runs?cursor=$OTHER_TENANT_CURSOR" | head -1   # 400, same body
```

---

## Scenario 3 — one tenant sees nothing of another (SC-007)

Submit byte-identical documents under two tenants, then:

```bash
curl -s -H "Authorization: Bearer $KEY_A" localhost:8000/v1/runs | jq '.runs[].run_id'
curl -s -H "Authorization: Bearer $KEY_B" localhost:8000/v1/runs | jq '.runs[].run_id'
curl -si -H "Authorization: Bearer $KEY_B" localhost:8000/v1/runs/$A_RUN_ID | head -1   # 404
```

**Expected**: disjoint lists, and tenant B's direct lookup of tenant A's run is a `404` identical to
one for an identifier that never existed.

---

## Scenario 4 — the administrative area is absent, not forbidden (SC-008)

With a non-administrative key:

```bash
curl -so /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $PLAIN_KEY" \
  "localhost:8000/v1/admin/credentials?tenant_id=docdoc-scope-probe"     # 404
curl -so /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $ADMIN" \
  "localhost:8000/v1/admin/credentials?tenant_id=docdoc-scope-probe"     # 200, empty list
```

Both verified 2026-09-11. That pair **is** the console's administrative probe: a tenant holding
nothing answers `200` to an administrator and `404` to everyone else, which is the whole question,
and it needs no route reporting the caller's own scope.

**Expected**: `404`, never `403`, and the console shows no credentials or erasure area at all. In the
browser, confirm the navigation has no entry for either — hidden, not disabled.

---

## Scenario 5 — issue, then revoke the key in use (SC-010, FR-026a)

The first administrative credential is a command-line act (ADR-0016 §5), and the flag is `--admin`:

```bash
docker compose -f packaging/docker/compose.yml exec -T api \
  docdoc credential issue --tenant acme --admin --json      # prints the key once

# The listing requires a tenant. Without one it answers 422, not 200 — which is
# what made the console's administrative probe read "not an administrator" for
# an administrator until T072 found it.
curl -s -H "Authorization: Bearer $ADMIN" \
  "localhost:8000/v1/admin/credentials?tenant_id=acme"
```

A revocation takes effect on the next request: verified 2026-09-11 as `200` before and `401` after,
with no restart.

In the console with an administrative key: issue a credential, confirm it is shown once, reload the
listing and confirm the key itself is nowhere in it.

Then revoke the key currently in use. **Expected**: the warning appears before the first revocation
of the session; after revoking, the next action is refused, the console drops the key and asks for
another. Nothing reports the revocation itself as having failed.

---

## Scenario 6 — erasure is deliberate (SC-011)

With two tenants holding byte-identical documents, erase one from the console.

**Expected**: the action stays unavailable until the typed identifier matches character for
character; the reported counts are the deployment's own; the other tenant's runs still resolve and
still reuse their artifacts.

The response carries `{"scope", "target", "runs", "artifacts", "blobs", "rows", "degraded"}` — the
console shows the four counts and says "nothing was there to remove" when they are all zero, which is
what erasing an absent tenant legitimately returns (FR-032, verified 2026-09-11). Then open an erased run: it reports as removed with its policy and
timestamp, and nothing else.

---

## Scenario 7 — the idle bound (SC-003a)

Leave the tab untouched for the documented bound, return, and act.

**Expected**: the console has already discarded the key, asks for it again, and made no request with
the discarded one. The model's own test covers the decision without waiting 15 minutes:

```bash
npm --prefix ui test
```

---

## Scenario 8 — the fences (SC-006, SC-014a, SC-012)

```bash
npm --prefix ui run lint:boundaries   # read-only, model boundary, console boundary, console a11y
npm --prefix ui run licenses          # no new obligation
uv run pytest tests/contract/test_no_review_platform.py -q
git diff --stat main -- tests/contract/test_no_review_platform.py   # empty: FR-003
```

**Expected**: all clean, and the last command prints nothing. A modified
`test_no_review_platform.py` means the milestone changed the thing that was measuring it.

---

## Scenario 9 — the operator target (SC-015)

Hand someone a key and the URL, with a named failed run somewhere in the list, and time them to
"here is why it failed". **Expected**: under three minutes, with no documentation open.

This is the only scenario here with a human in it, and it is the one that will find the problems the
other eight cannot: a status column that reads as a colour with no label, a filter that resets on
paging, an error that says `ExtractionError` and nothing else.
