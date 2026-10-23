# ADR-0023: Console Login and Cross-Tenant Operations

- **Status**: Accepted
- **Date**: 2026-10-20
- **Amended in part by**: [ADR-0026](0026-tenant-quality-evaluation.md) §6 (§4's page images
  are also served for succeeded extraction runs)
- **Supersedes in part**: [ADR-0019](0019-operations-console-boundary.md) §3 ("the console introduces
  no server-side concept") and §4 (the pasted, never-persisted key), and `specs/011`'s FR-022a and
  FR-037 (no part of a result is rendered); [ADR-0020](0020-retire-the-grounding-viewer.md) in part
  (a page-and-box viewer returns, scoped to OCR runs and behind this login)
- **Relates to**: [ADR-0014](0014-tenant-scoping-and-store-namespacing.md) (tenant scoping, and the
  existence oracle this must not reopen), [ADR-0016](0016-credential-lifecycle.md) (credential
  lifecycle; the admin scope this leaves untouched), [ADR-0017](0017-confidence-routing-policy.md)
  (the two routing outcomes; still the only disposition)
- **Design**: `docs/superpowers/specs/2026-10-20-console-upgrade-design.md`
- **Principles engaged**: IX (Evaluation and human correction are product features — the review
  platform fence), X (Layered dependency direction and bounded concepts — the new route family),
  XI (MVP discipline and scale through boundaries)

## Context

docdoc is now an OCR gateway for mmerp (`2026-10-19-ocr-gateway-design.md`,
`2026-10-19-async-ocr-queue-design.md`), and the people who run it check OCR and extraction runs
every day. The console they have does not let them: it sees one tenant at a time through a pasted
API key, cannot show what OCR produced, and renders no part of a result by ADR-0019 §4 and
`specs/011` FR-022a/FR-037. Operators leave the console for `curl` and logs, which is the thing the
console existed to make unnecessary.

The approved upgrade design asks for three things that each cross a line one of those ADRs drew on
purpose: a login that replaces the pasted key, reads that cross every tenant instead of the one a
key resolves to, and a renderer for OCR results including the page image behind them. ADR-0019 §4
rejected a cookie session by name, reasoning that it would add four new security surfaces to guard
a credential the deployment already has; ADR-0020's own closing argument, declining to merge a
grounding surface into the console, was that if one ever returned it should live "inside the
console's session, not beside it" — anticipating this decision without making it. Reinterpreting
either ADR's text to mean something narrower than it says would be the precedence inversion v1.6.0,
v1.8.0, and v1.9.0 each warned against in themselves: the fix is an ADR that supersedes, not a
reading that argues the old one already allowed this.

## Decision

### 1. Console accounts and sessions

An operator authenticates with a username and password; there is no other console credential, and
pasting an API key into the console is gone. Accounts exist only through
`docdoc console-user add|passwd|disable|list` — the console itself has no account-management
screen. A username matches `^[a-z0-9_.-]{1,64}$`; login lower-cases and trims what is typed, so a
username is compared the same way it was created. A password must be at least 12 characters and is
never stored: `hashlib.scrypt` is run over it and only the derived hash is kept.

A successful login opens a session row in Postgres and sets a cookie named `docdoc_console`:
`HttpOnly`, `SameSite=Strict`, `Path=/v1/console`, and `Secure` whenever the request arrived over
https. The cookie carries an opaque session token; Postgres stores `sha256(token)` and never the
token itself, so a dumped sessions table does not hand over a usable session. The session has an
idle bound, `DOCDOC_CONSOLE_IDLE_SECONDS` (default 900 seconds), enforced server-side on every
request that resolves it — the clock is the server's, not the cookie's claimed age.

Five consecutive failed logins for a username lock that username for five minutes, counted against
the username regardless of where the attempts came from. Wrong username, wrong password, a locked
account, and a disabled account all answer with one identical refusal body, so the shape of the
answer never tells an attacker which of the four they hit.

Every non-`GET`/`HEAD` request under `/v1/console` must carry the header `X-Docdoc-Console: 1` or is
refused `403`, independent of whether the session itself is valid. `SameSite=Strict` already stops
a cross-site browser from attaching the cookie to begin with; the header closes the remaining gap —
a plain cross-site form post cannot set it — so a state-changing console request cannot be forged
from another site by either mechanism failing alone.

### 2. One level of access

Signed in means signed in to everything: every tenant, every run, and every admin area, with no
role layering between operators. ADR-0016 §6's admin scope is unmoved by this — a console session
is a different credential with a different blast radius from a Bearer key, not a wider version of
the same scope, and signing in does not grant it. Issuing an admin-scoped key stays something only
the CLI does; a console session is never the thing that makes `docdoc credential issue --admin`
unnecessary.

### 3. A separate route family

`/v1/console/*` answers only to the session cookie; the rest of `/v1` answers only to a Bearer key.
Neither credential opens the other's doors: a Bearer key presented at a console route is refused,
and a console session presented outside `/v1/console` is refused. This keeps ADR-0014 §3's isolation
true on both sides of the new line, and the reason is concrete rather than procedural — a key leaked
out of mmerp must not become a key to every tenant, which is exactly what it would become if a
Bearer key could reach a route that was written assuming the caller may see everything.

### 4. The console renders results

The console may now render an OCR run's `Document`, an extraction run's result JSON, and — Plan 3 of
the upgrade design — a page image with a box per token for an OCR run, so an operator can see what
was read laid over the page it was read from. This is why ADR-0020 is partly superseded: the
grounding viewer is not revived, but the thing its closing argument anticipated — a result renderer
returning "inside the console's session" — has, under the session §1 builds rather than a
hypothetical one. The page-image route this needs renders a page the deployment already stored; it
is not a new place a result is produced or kept, and it answers only to a console session.

### 5. What stays forbidden

ADR-0019 §2's five nouns are unchanged: reviewer assignment, a review queue, workload, a review
state beyond the closed run status set, and a disposition beyond ADR-0017's two routing outcomes.
Nothing in this ADR adds a way to assign, queue, or dispose, and the Overview screen the design adds
counts runs by kind and status, never by person, for exactly that reason.

### 6. Consequences

`ui/scripts/check-console-boundary.mjs`'s `RESULT_CONTENT` rule set, which failed the build on the
identifiers `specs/011` FR-022a and FR-037 forbade because no part of a result could be rendered,
is retired — a console now allowed to render a result cannot be guarded by a script whose job was
forbidding that. `tests/contract/test_console_adds_one_read.py`'s `SESSION_WORDS` list and its
`test_no_route_names_a_session_mechanism`, which failed on `login`, `session`, `token`, and the rest
because FR-009 forbade all of them, are retired for the same reason. Both are replaced by tests of
the boundary this ADR actually draws: a tenant or admin Bearer key must not reach `/v1/console`, a
console session must not reach a route outside it, and ADR-0019 §2's five nouns stay absent from the
enlarged console. `REVIEW_PLATFORM`'s rule set in the same script is unchanged by any of this and
keeps running.

Accepted beyond the guards: the console now requires the run database unconditionally, because
accounts and sessions live there, where before it worked against any deployment holding a key; an
operator signs in once and sees every tenant, where a reload used to ask for a key scoped to one;
and a result is reachable from the console at all, with the five nouns of §5 as the only remaining
fence — there is no second one behind it if that word list is ever wrong.

## Data model

Migration `0009_console_accounts.sql`. Two tables, both new; nothing here reuses or alters a table
Milestone 9 or 10 created.

### `console_users`

One row per operator. `docdoc console-user add|passwd|disable` is the only writer; the console
itself has no account-management screen (§1).

| Column | Type | Notes |
|---|---|---|
| `username` | `text` primary key | matches `^[a-z0-9_.-]{1,64}$`; compared as given — `login` lower-cases and trims what it is handed, `add` does not |
| `password_hash` | `text` not null | `hashlib.scrypt`, self-describing: `scrypt$16384$8$1$<salt>$<key>`. **Never the password itself** |
| `created_at` | `timestamptz` not null | |
| `disabled_at` | `timestamptz`, nullable | set by `disable`; there is no "enable" — a disabled account is re-created by `console-user add`, which writes the new password and clears `disabled_at`, `failed_logins`, and `locked_until` on the same row. `passwd` refuses a disabled account and names `add` |
| `failed_logins` | `int` not null, default `0` | consecutive failures; reset to `0` on a successful login, by the same failure that sets `locked_until` (§1), or by `passwd`/`add` (which also clear `locked_until`) |
| `locked_until` | `timestamptz`, nullable | past this instant the account is no longer locked; the comparison is against the server's clock, never a value the client supplies |

### `console_sessions`

One row per live session; a row's absence *is* "not signed in" — there is no soft-deleted or
expired state to distinguish from a row that was never created.

| Column | Type | Notes |
|---|---|---|
| `digest` | `text` primary key | `sha256(token)` (`principal.digest_of`). **Never the token itself** — a dumped table does not hand over a usable session |
| `username` | `text` not null, references `console_users` on delete cascade | |
| `created_at` | `timestamptz` not null | |
| `last_seen_at` | `timestamptz` not null | the idle clock; compared against the server's `now` on every request that resolves the session, touched on each live read |

An index, `console_sessions_by_user (username)`, is what `disable` and `set_password` use to delete
every session belonging to the account they just acted on.

## Alternatives considered

- **Keep the pasted key and add cross-tenant reads behind it.** Rejected: a key authenticates one
  tenant by construction, and widening what it can see would mean a tenant's own key also seeing
  every other tenant — the isolation break ADR-0014 exists to prevent — or a second, wider key type,
  which is a second credential ADR-0019 §4 already argued against.
- **An authenticating proxy in front of the console, as ADR-0019 §4 suggested for single sign-on.**
  Rejected for this decision specifically because it solves "who may open the console", not
  "who the console shows what to" — every operator behind such a proxy would still see every tenant
  with no session of their own to scope or expire.
