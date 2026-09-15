# Data Model: The Operations Console

**Feature**: `specs/011-operations-console/` | **Date**: 2026-09-11

**Nothing here is persisted by this milestone.** No table, no column, no index, no migration. Two of
these entities are projections of rows Milestone 9 and 10 already write; the rest live in one browser
tab and end with it. That is the whole point of the milestone and this document is the evidence.

---

## Server side

### `RunSummary` — one row of the listing

A projection of the `runs` row, narrowed to what a list can show without disclosing content.

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `run_id` | UUID | `runs.run_id` | the primary key; also half the cursor |
| `status` | enum(5) | `runs.status` | `queued` / `running` / `succeeded` / `failed` / `cancelled`. The closed set, unchanged (FR-004) |
| `created_at` | timestamptz | `runs.created_at` | submission time; the other half of the cursor |
| `finished_at` | timestamptz \| null | `runs.updated_at` when terminal | absent while the run is not terminal |
| `blob_id` | string | `runs.blob_id` | the document identity the operator already holds |
| `schema` | string | `runs.schema_identity` | the identity `name@version`, not the schema's contents. The column is `schema_identity`; the field is `schema`, and the two names are written out here because a projection that renames a column is where a wrong one goes unnoticed |
| `processing_id` | string \| null | `runs.processing_id` | **present only on a succeeded run**, and only as an identifier. `processing_id_belongs_to_success` makes it exactly the link FR-022a permits — so it is a row in this table and not a remark under it |

**Not present, deliberately**: `stage_outcomes`, `processing_id`'s result body, any extracted value,
any claimed text, any error message carrying document content. FR-019 fixes this list, and the
absence is what lets the listing be shown to anyone holding the tenant's key.

**`processing_id` is a field above and not a footnote**, because T035 builds the model from that table
and a link FR-022a requires would otherwise be read as optional prose and left out.

**Validation**: none performed here. Every field is read from a row that a `CHECK` constraint and the
`Run` model already validated on the way in; re-validating on the way out would be a second opinion
about a row the database has already agreed with.

### `RunPage` — what the route returns

| Field | Type | Notes |
|-------|------|-------|
| `runs` | `RunSummary[]` | newest first; at most `limit` entries |
| `next_cursor` | string \| null | `null` means this is the last page — an explicit end, not an empty page discovered on the next request |

### `PageCursor` — an opaque position

Not stored anywhere. Encoded as base64url over `{"t": tenant_id, "c": created_at, "i": run_id}`, against the columns
`runs.created_at` and `runs.run_id`
(research R3).

**Rules**:

- Decoded and compared against the caller's tenant. A mismatch is refused with the same answer a
  malformed cursor gets, so a cursor cannot be used to learn that another tenant exists (FR-016).
- Never signed. A forged cursor can only name a position inside the forger's own tenant, because the
  query's tenant predicate comes from the credential and not from the cursor (FR-014).
- Carries no offset, no count, and no total. A total would be a second query on every page and a
  number that is wrong by the time it is read.

### State transitions

**None.** This milestone adds no state machine and no transition. `RunStatus` keeps the five members
`specs/009` FR-006 closed it at, and a removed run is reported as no longer being a run rather than
as a sixth state (`specs/010` FR-004a). The listing simply omits removed runs (FR-020); the tombstone
remains reachable by identity, by its owner, exactly as it was.

---

## Browser side

Every entity below exists in one tab, in memory, and is unrecoverable after the tab closes or
reloads. SC-004 measures that as zero bytes in any storage mechanism.

### `Session` — the credential, and the only thing resembling identity

| Field | Type | Notes |
|-------|------|-------|
| `credential` | string \| null | the pasted key. `null` before entry and after expiry |
| `mode` | `"authenticated"` \| `"open"` | `open` when the deployment accepted a request with no credential (research R6) |
| `administrative` | boolean | discovered by one probe of the credential listing (research R7); `false` hides every administrative area |
| `last_activity_at` | timestamp | the only input to expiry besides `now` |

**Rules**:

- `expired(session, now)` is a pure function: `now - last_activity_at > 15 minutes` (FR-009a). It is
  in the model so a test can reach it; the shell supplies `now`.
- On expiry, and on any response reporting the credential was not accepted, `credential` returns to
  `null` and the console behaves exactly as it does before a key is entered (FR-010).
- The session has **no identifier**. Nothing server-side knows it exists, so there is nothing to
  revoke, expire, or clean up, and two tabs share nothing.

### `ListingState` — what the run list is showing

| Field | Type | Notes |
|-------|------|-------|
| `status_filter` | one of the five, or none | never a free-text query |
| `cursor_stack` | `string[]` | the cursors already visited, so "back" is a pop rather than a second cursor direction |
| `page` | `RunPage` \| loading \| failed | three states, distinguished, because an empty list and a failed request read identically if they are not (Edge Cases) |

`cursor_stack` is the one piece of client state with a shape worth defending: keeping visited cursors
means the server needs only a forward cursor, which halves what the route has to encode and makes
FR-018's "exactly once" a property of a single ordering rather than of two.

### `ErasureIntent` — a target and the operator's re-entry of it

| Field | Type | Notes |
|-------|------|-------|
| `target` | string | the tenant or document identifier |
| `typed` | string | what the operator typed back |
| `armed` | boolean | derived: `typed === target`, and derived **in the model** |

The comparison lives in the model for one reason: it is one `===` away from being decorative, and
FR-030 is the only thing standing between a mis-click and a tenant's data. A test reaches it there.

The intent is discarded when the credential is (Edge Cases), because a confirmation that survived a
re-authentication would be a confirmation the current operator never gave.

### `CredentialRecord` — one row of the credential listing

The shape the route actually returns, **learned from a running deployment rather than from a
document** (T072). The first version of this model had a singular `scope: string | null` and no
`label`; nothing disagreed with it until a real listing arrived.

| Field | Type | Notes |
|-------|------|-------|
| `credential_id` | UUID | |
| `tenant_id` | string | |
| `label` | string \| null | what it is for. Shown as `—` when absent |
| `scopes` | string[] | **plural**. `[]` is an ordinary credential; `["admin"]` is an administrative one |
| `created_at` | timestamptz | |
| `last_used_at` | timestamptz \| null | |
| `revoked_at` | timestamptz \| null | the fact; `isActive()` is the reading of it |

**No key and no digest** (`specs/010` FR-030). Either would make the listing a way to obtain what it
describes. `isActive` is derived rather than stored, because `revoked_at` is what the deployment
records and two fields would eventually disagree.

### `Delivery` — three states, not a table that is sometimes empty

| State | Means |
|-------|-------|
| `none` | no callback was registered when the run finished. Nothing was owed |
| `attempting` | attempts have been made and more may follow |
| `at-rest` | delivered, or exhausted (ADR-0018). Carries `delivered: boolean` |

Three states rather than an attempt list that is occasionally empty, because *"no destination was
registered"* and *"a destination was registered and nothing has been tried yet"* are different
answers to the operator's actual question — **why did my client never hear about this** (FR-034).

An `Attempt` carries `attempted_at`, `status` (`null` when the attempt got no answer at all), and
`outcome`. **No signing secret reaches this model**: the route does not return one and there is
nowhere to put it (FR-035).

### `Failure` — which of five things went wrong

The console invents no error vocabulary (FR-039). What this adds is only *which kind happened*:

| Kind | Means | Console's response |
|------|-------|--------------------|
| `reported` | the deployment answered and said no | show its own message |
| `unauthenticated` | the credential was refused (`401`) | drop the key, ask again (FR-010) |
| `transport` | no usable answer arrived | say so; nothing is known about the deployment's view |
| `removed` | `410` — the run was here and is gone | show `deletedAt` and `policy`, nothing else (FR-023) |
| `absent` | `404` on an administrative route | hide the area; never explain it (FR-028, ADR-0016 §6) |

`removed` and `absent` exist because a `410` and a `404` mean different things to this console and
the same thing to a generic error handler. `removed` carries the tombstone's two fields: it is where
FR-023 lives, and parsing them in a component would put the requirement where no test reaches it.

### `CredentialDisclosure` — the key shown once

| Field | Type | Notes |
|-------|------|-------|
| `key` | string | present only in the response to an issuance |
| `acknowledged` | boolean | once true, the key is dropped from memory |

**Rules**: FR-024 shows it exactly once with the warning that it will not be shown again; FR-012
forbids it appearing anywhere else. The console **cannot** identify which listed credential is its
own (FR-026a), so nothing joins this disclosure back to a row in the listing.
