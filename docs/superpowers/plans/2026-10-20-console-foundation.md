# Console Foundation (Plan 1 of 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators sign in to `/console` with a username and password, see every tenant, and do
everything the console does today without an API key. Today's broken bits are fixed along the
way.

**Architecture:**
- **Governance first.** ADR-0023 and constitution v1.12.0 lift ADR-0019's no-login,
  no-result, and one-read rules.
- **Accounts and sessions.** Console accounts and sessions live in two new Postgres tables, behind
  a store in `docdoc.runs`. A CLI manages accounts.
- **A separate route family.** `/v1/console/*` sits on its own router, gated in the existing
  middleware by an `HttpOnly` session cookie plus a custom header on writes. Bearer keys never
  reach it, and console sessions never reach the rest of `/v1`.
- **The frontend** swaps the key-paste flow for a login form and points every request at the
  console routes.

**Tech Stack:** Python 3 / FastAPI / psycopg / argparse; React 19 + TypeScript + Vite 8 + Astryx; `node --test`; pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-console-upgrade-design.md`

**This plan covers:**
- governance
- the fixes listed under the spec's "Fix what is broken today"
- login, sign-out, and session expiry
- a cross-tenant run list and run detail, reaching today's screens and adding a tenant column
- credentials and erasure without a tenant key

**The later plans:**
- **Plan 2, monitoring:** `started_at`/`finished_at`, failure reason, Overview, sidebar, kind and
  tenant filters, polling, Cancel.
- **Plan 3:** the page-image viewer and rotation.
- **Plan 4:** Submit OCR, Parsers, Tenants, Callbacks.

## Global Constraints

- **Clock and randomness.** Nothing under `src/docdoc/runs/` other than `identity.py` may import
  `datetime`, `time`, `uuid`, `random`, or `secrets` for reading. Tokens, salts, and `now` come from
  `docdoc.runs.identity` and are passed down as parameters (`tests/unit/test_runs_clock_confinement.py`).
  `test_clock_guard_was_not_widened.py` forbids widening `PERMITTED`.
- **Migrations.** These are plain idempotent SQL `NNNN_name.sql` files, never edited once applied.
  The next number is `0009`.
- **No new Python or npm dependency.** Passwords use `hashlib.scrypt`.
- **The session cookie:**
  - is named `docdoc_console`, with `HttpOnly`, `SameSite=Strict`, `Path=/v1/console`;
  - carries `Secure` when `request.url.scheme == "https"`;
  - holds a token from `identity.new_console_token()`; only `sha256(token)` (`principal.digest_of`)
    is stored.
- **Idle expiry** comes from `DOCDOC_CONSOLE_IDLE_SECONDS`, default `900`, and is enforced on the
  server.
- **Forged-request protection:** every non-GET/HEAD request under `/v1/console` must carry
  `X-Docdoc-Console: 1`, otherwise `403`. The frontend sends it on every console request.
- **Usernames** match `^[a-z0-9_.-]{1,64}$`. Login lower-cases and trims the username it is given.
  Passwords need at least 12 characters.
- **Lockout:** 5 consecutive failed logins lock an account for 5 minutes. The refusal body is the
  same for a wrong user, a wrong password, a locked account, and a disabled account.
- **Route words.** Route paths must avoid the words `tests/contract/test_no_review_platform.py`
  forbids, `queue` included. Console source must not contain the five nouns
  `check-console-boundary.mjs` checks.
- **Visible console text is English.**
- **Logging destructive actions.** Every destructive console action calls the existing `_audit`
  helper or logs through `_erase`, with `actor="console:<username>"`.

## Review Focus

1. **A Bearer key on `/v1/console/*`**, tenant or admin, valid or not, gets `401`, never data.
   Test: Task 4, `test_a_bearer_key_never_opens_the_console_api`.
2. **A disabled account, or one whose password changed, loses its live sessions immediately.**
   Test: Task 2, `test_disable_and_passwd_end_live_sessions`.
3. **A session that goes idle mid-task** makes the next request `401`, and the page returns to the
   login form saying why. Test: Task 2, `test_an_idle_session_is_refused_and_removed`. Model test:
   Task 7, `test_a_refusal_signs_the_operator_out_with_a_notice`.
4. **A cursor from one tenant filter replayed under another filter** gets `400 InvalidCursor`, not
   rows from the wrong page. Test: Task 5, `test_a_cursor_is_bound_to_its_tenant_filter`.
5. **A deployment with no run database** answers `503 ConsoleUnavailable` from the console API, and
   the login form shows that sentence instead of "wrong password". Tests: Task 4,
   `test_without_a_run_database_the_console_says_so`; Task 7, `test_unavailable_is_not_a_wrong_password`.

---

### Task 1: ADR-0023 and constitution v1.12.0

**Files:**
- Create: `docs/adr/0023-console-login-and-cross-tenant-operations.md`
- Modify: `.specify/memory/constitution.md` (Sync Impact Report at the top, the "A full review
  UI…" paragraph near line 904, and the version line at the end)
- Modify: `docs/adr/0019-operations-console-boundary.md` and
  `docs/adr/0020-retire-the-grounding-viewer.md` (Status line only: "Partly superseded by ADR-0023")
- Modify: `docs/adr/README.md` (index entry)

**Interfaces:** none (documentation). Later tasks cite "ADR-0023 §N" in docstrings, so keep the
section numbers below.

- [ ] **Step 1: Write ADR-0023** in the format of ADR-0022: Status Accepted, Date 2026-10-20,
  Supersedes in part ADR-0019 §3, §4 and FR-022a/FR-037, and ADR-0020 in part. Link the design
  spec. Sections:
  - §1 **Console accounts and sessions.** Username/password, accounts from the CLI only,
    sessions in Postgres, the cookie attributes and idle bound from Global Constraints, lockout,
    and the custom-header rule.
  - §2 **One level of access.** A signed-in operator sees every tenant and every admin area.
    Issuing an admin-scoped key stays CLI-only.
  - §3 **A separate route family.** `/v1/console/*` is reachable only by session, and the rest of
    `/v1` only by Bearer key. This keeps ADR-0014's isolation for API keys, and the reason is that
    a key leaked from mmerp must not become a key to every tenant.
  - §4 **The console renders results**: an OCR document, the extraction result JSON, and (Plan 3)
    page images with boxes for OCR runs. This is why ADR-0020 is partly superseded: the viewer
    returns behind authentication.
  - §5 **What stays forbidden**: ADR-0019 §2's five nouns, unchanged.
  - §6 **Consequences.** The `RESULT_CONTENT` rule set in `check-console-boundary.mjs` and the
    "no session words" contract test are retired and replaced by tests of the new boundary.
- [ ] **Step 2: Amend the constitution to 1.12.0 (MINOR).**
  - Add an `AMENDMENT 1.11.0 → 1.12.0 (2026-10-20)` block to the Sync Impact Report in the style of
    the existing blocks: principles affected, rationale, sections amended, migration note,
    templates.
  - Rewrite the console paragraph so the permitted list adds: a username/password login for
    operators, cross-tenant listing and inspection, rendering run results, and the routes those
    need.
  - Keep the five-noun sentence verbatim.
  - Update the footer to `**Version**: 1.12.0 | … | **Last Amended**: 2026-10-20`.
- [ ] **Step 3: Update the ADR-0019, ADR-0020 status lines and the ADR index.**
- [ ] **Step 4: Commit**

```bash
git add docs/adr .specify/memory/constitution.md
git commit -m "docs(adr): ADR-0023 console login and cross-tenant operations; constitution 1.12.0"
```

---

### Task 2: Console accounts store

**Files:**
- Create: `src/docdoc/runs/migrations/0009_console_accounts.sql`
- Create: `src/docdoc/runs/console_accounts.py`
- Create: `tests/fixtures/console_accounts.py`
- Modify: `src/docdoc/runs/identity.py` (three functions)
- Modify: `src/docdoc/api/settings.py` (re-export `CONSOLE_IDLE_ENV` next to `RUN_CREDENTIAL_TTL_ENV`)
- Test: `tests/unit/test_console_accounts.py`, `tests/integration/test_console_accounts_postgres.py`

**Interfaces:**
- Produces, in `docdoc.runs.identity`:
  - `CONSOLE_IDLE_ENV = "DOCDOC_CONSOLE_IDLE_SECONDS"`
  - `new_console_token() -> str`, which is `secrets.token_urlsafe(32)`
  - `new_password_salt() -> bytes`, which is `secrets.token_bytes(16)`
  - `configured_console_idle(explicit: int | None = None) -> timedelta`, default 900 s, built with
    the same `_positive_int` helper the other `configured_*` functions use
- Produces, in `docdoc.runs.console_accounts`:
  - constants: `USERNAME_PATTERN: re.Pattern[str]`, `MIN_PASSWORD_LENGTH = 12`,
    `MAX_FAILED_LOGINS = 5`, `LOCKOUT = timedelta(minutes=5)`
  - `class ConsoleAccountError(ValueError)`
  - `@dataclass(frozen=True) class ConsoleAccount`, with fields `username: str`,
    `created_at: datetime`, `disabled_at: datetime | None`
  - `hash_password(password: str, *, salt: bytes) -> str`. The format is
    `scrypt$16384$8$1$<b64 salt>$<b64 key>`.
  - `verify_password(password: str, encoded: str) -> bool`, constant time via
    `hmac.compare_digest`
  - `normalise_username(raw: str) -> str`, which is `raw.strip().lower()`
  - `ConsoleAccounts`, a Protocol implemented by both stores below, with these methods:
    - `add(username: str, password: str, *, salt: bytes, now: datetime) -> ConsoleAccount`. It
      raises `ConsoleAccountError` on a bad username, a short password, or a duplicate.
    - `set_password(username: str, password: str, *, salt: bytes, now: datetime) -> None`. It
      raises on an unknown user and deletes that user's sessions.
    - `disable(username: str, *, now: datetime) -> bool`. It returns `True` if the account was
      active and deletes that user's sessions.
    - `accounts() -> tuple[ConsoleAccount, ...]`
    - `login(username: str, password: str, *, token: str, now: datetime) -> bool`. On success it
      stores a session under `digest_of(token)` and resets the failure counter. On failure it
      counts and locks.
    - `resolve(token: str, *, now: datetime, idle: timedelta) -> str | None`. It returns the
      username and touches `last_seen_at`. An idle session, or one whose user is disabled, is
      deleted and gives `None`.
    - `logout(token: str) -> None`
  - `@dataclass class PostgresConsoleAccounts(execute)`, with the same `execute` shape as
    `PostgresKeyStore`
- Produces, in `tests/fixtures/console_accounts.py`: `InMemoryConsoleAccounts()`, with identical
  behaviour.
- Lockout and idle decisions are module functions both stores call, so the policy is written
  once:
  - `locked(failed_logins: int, locked_until: datetime | None, now: datetime) -> bool`
  - `after_failure(failed_logins: int, now: datetime) -> tuple[int, datetime | None]`
  - `alive(last_seen_at: datetime, now: datetime, idle: timedelta) -> bool`

- [ ] **Step 1: Write the failing unit tests** in `tests/unit/test_console_accounts.py`,
  parametrised over `InMemoryConsoleAccounts`:

```python
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
IDLE = timedelta(seconds=900)
SALT = b"s" * 16

def test_a_hash_verifies_only_its_own_password():
    encoded = hash_password("correct horse battery", salt=SALT)
    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("correct horse batterz", encoded)

def test_add_refuses_bad_usernames_short_passwords_and_duplicates(accounts):
    for bad in ("Alice", "a b", "", "x" * 65):
        with pytest.raises(ConsoleAccountError):
            accounts.add(bad, "long enough pass", salt=SALT, now=NOW)
    with pytest.raises(ConsoleAccountError):
        accounts.add("alice", "short", salt=SALT, now=NOW)
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    with pytest.raises(ConsoleAccountError):
        accounts.add("alice", "long enough pass", salt=SALT, now=NOW)

def test_login_then_resolve_names_the_operator(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    assert accounts.login(" Alice ", "long enough pass", token="t1", now=NOW)
    assert accounts.resolve("t1", now=NOW + timedelta(seconds=10), idle=IDLE) == "alice"
    assert accounts.resolve("unknown", now=NOW, idle=IDLE) is None

def test_an_idle_session_is_refused_and_removed(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="t1", now=NOW)
    assert accounts.resolve("t1", now=NOW + IDLE, idle=IDLE) is None
    assert accounts.resolve("t1", now=NOW, idle=IDLE) is None  # gone, not merely stale

def test_activity_extends_the_session(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="t1", now=NOW)
    accounts.resolve("t1", now=NOW + timedelta(seconds=800), idle=IDLE)
    assert accounts.resolve("t1", now=NOW + timedelta(seconds=1600), idle=IDLE) == "alice"

def test_five_failures_lock_for_five_minutes(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    for _ in range(5):
        assert not accounts.login("alice", "wrong password!", token="x", now=NOW)
    assert not accounts.login("alice", "long enough pass", token="t1", now=NOW + timedelta(minutes=4))
    assert accounts.login("alice", "long enough pass", token="t2", now=NOW + timedelta(minutes=5))

def test_disable_and_passwd_end_live_sessions(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.add("bob", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="a", now=NOW)
    accounts.login("bob", "long enough pass", token="b", now=NOW)
    assert accounts.disable("alice", now=NOW) is True
    accounts.set_password("bob", "another long pass", salt=SALT, now=NOW)
    assert accounts.resolve("a", now=NOW, idle=IDLE) is None
    assert accounts.resolve("b", now=NOW, idle=IDLE) is None
    assert not accounts.login("alice", "long enough pass", token="a2", now=NOW)
```

- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/unit/test_console_accounts.py -v`
  Expected: FAIL with `ModuleNotFoundError: docdoc.runs.console_accounts`.
- [ ] **Step 3: Add the identity functions, the module, and the in-memory fixture.**
  - `login` for an unknown username still runs `verify_password` against a module-level dummy
    hash, so that it takes the same time as a wrong password.
  - The module docstring cites ADR-0023 §1.
- [ ] **Step 4: Run the unit tests and confirm they pass**, along with the clock guard.
  Run: `uv run pytest tests/unit/test_console_accounts.py tests/unit/test_runs_clock_confinement.py tests/unit/test_clock_guard_was_not_widened.py -v`
  Expected: PASS.
- [ ] **Step 5: Write migration `0009_console_accounts.sql`.**
  - `console_users`: `username text PRIMARY KEY`, `password_hash text NOT NULL`,
    `created_at timestamptz NOT NULL`, `disabled_at timestamptz`,
    `failed_logins int NOT NULL DEFAULT 0`, `locked_until timestamptz`.
  - `console_sessions`: `digest text PRIMARY KEY`,
    `username text NOT NULL REFERENCES console_users ON DELETE CASCADE`,
    `created_at timestamptz NOT NULL`, `last_seen_at timestamptz NOT NULL`.
  - An index `console_sessions_by_user (username)`.
  - Everything uses `IF NOT EXISTS`, and the header comment cites ADR-0023.
- [ ] **Step 6: Write `tests/integration/test_console_accounts_postgres.py`.** It reuses the same
  test functions against `PostgresConsoleAccounts`: import them from the unit module and supply a
  fixture that migrates and runs `TRUNCATE console_sessions, console_users`, following the
  `queue` fixture in `tests/integration/test_delivery_postgres.py`.
  Run: `uv run pytest tests/integration/test_console_accounts_postgres.py -v`
  Expected: PASS with `DOCDOC_TEST_DATABASE_URL` set, SKIP without it.
- [ ] **Step 7: Commit**

```bash
git add src/docdoc/runs/console_accounts.py src/docdoc/runs/migrations/0009_console_accounts.sql src/docdoc/runs/identity.py src/docdoc/api/settings.py tests/fixtures/console_accounts.py tests/unit/test_console_accounts.py tests/integration/test_console_accounts_postgres.py
git commit -m "feat(console): accounts and sessions store"
```

---

### Task 3: `docdoc console-user add|passwd|disable|list`

**Files:**
- Create: `src/docdoc/cli/commands/console_user.py`
- Modify: `src/docdoc/cli/__init__.py` (parser next to `credential`, and dispatch next to line ~415)
- Test: `tests/integration/test_operations_cli.py` (append), `tests/contract/test_cli_contract.py` if it enumerates subcommands

**Interfaces:**
- Consumes: `PostgresConsoleAccounts`, `identity.new_password_salt`, `identity.now`.
- Produces: `run(args: argparse.Namespace, settings: Settings) -> Rendering`, with the same shape
  as `commands/credential.py`, and `_execute(dsn)` copied from that file's pattern.
- Arguments:
  - `add USERNAME`, `passwd USERNAME`, `disable USERNAME`, and `list`, each with
    `--run-database-url URL`.
  - The password is read with `getpass.getpass()` twice, and must match. Alternatively,
    `--password-stdin` reads one line from stdin for scripting.
  - The password never appears in argv, output, or logs.

- [ ] **Step 1: Write the failing tests**, Postgres-backed in the style of the existing
  `test_operations_cli.py` cases:
  - `test_console_user_add_then_list_shows_it_without_any_hash`: `add alice --password-stdin` with
    stdin `"long enough pass\n"` exits 0. `list --json` contains `{"username": "alice",
    "disabled": false}`, and the output contains no `scrypt$`.
  - `test_console_user_refuses_a_short_password`: exit code ≠ 0, and the message names 12
    characters.
  - `test_console_user_disable_is_idempotent`: the second `disable` exits 0 and reports
    "already disabled".
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/integration/test_operations_cli.py -k console_user -v`
  Expected: FAIL, because argparse reports an invalid choice.
- [ ] **Step 3: Implement the command and the parser wiring.** Without a DSN, raise
  `RunStateUnavailableError` with a sentence like the credential command's.
- [ ] **Step 4: Run the tests and confirm they pass.** Also run
  `uv run pytest tests/contract/test_cli_contract.py -v` and update its subcommand list if it
  enumerates subcommands.
- [ ] **Step 5: Commit**

```bash
git add src/docdoc/cli tests/integration/test_operations_cli.py tests/contract/test_cli_contract.py
git commit -m "feat(cli): docdoc console-user"
```

---

### Task 4: Session routes and the `/v1/console` gate

**Files:**
- Create: `src/docdoc/api/console.py`. This is the console router. It keeps `app.py` from growing
  past ~3000 lines, and this is its first new module.
- Modify: `src/docdoc/api/app.py`:
  - `_Deployment.__init__` gains `console: Any = None`
  - a new method `console_accounts()`
  - `_require_credential_everywhere_else` gains the gate
  - `build_app` gains `app.include_router(console_router())`
- Modify: `tests/contract/test_console_adds_one_read.py`:
  - add `CONSOLE_ADDED` to `ADDED`
  - replace `test_no_route_names_a_session_mechanism` with
    `test_session_words_appear_only_under_the_console_api`
  - relax `test_no_route_reports_the_callers_own_scope` to exempt `/v1/console/session`, citing
    ADR-0023 §1
- Modify: `tests/contract/test_http_ui_endpoints.py` (`ROUTES`)
- Test: `tests/contract/test_console_session.py`

**Interfaces:**
- Consumes: `ConsoleAccounts` (Task 2), `identity.new_console_token`, `identity.now`,
  `identity.configured_console_idle`.
- Produces, in `docdoc.api.console`:
  - constants: `CONSOLE_API_PREFIX = "/v1/console"`, `CONSOLE_COOKIE = "docdoc_console"`,
    `CONSOLE_HEADER = "X-Docdoc-Console"`
  - `console_router() -> APIRouter`, with prefix `/v1/console`. Every route except
    `POST /session` depends on `operator_of`.
  - `async def operator_of(request: Request) -> str`, which reads `request.state.operator` set by
    the gate and raises 401 when it is absent
  - `Operator = Annotated[str, Depends(operator_of)]`
  - `console_gate(request: Request, accounts: Any) -> JSONResponse | None`, which is called by the
    middleware for console paths. It returns a refusal or `None`, and sets
    `request.state.operator`.
- Produces, in `_Deployment`: `console_accounts() -> Any`. It returns the injected store, or else
  builds `PostgresConsoleAccounts(execute=self._runs._execute)` once, or returns `None` when there
  are no runs. This mirrors `corrections()`.
- **Gate order, for a path equal to `/v1/console` or starting with `/v1/console/`. The Bearer
  check is skipped entirely:**
  1. `accounts is None` gives `503 {"error":{"class":"ConsoleUnavailable","message":"the console needs a run-state database; set DOCDOC_RUN_DATABASE_URL and run docdoc migrate"}}`.
  2. A method other than GET or HEAD without `X-Docdoc-Console: 1` gives
     `403 {"error":{"class":"ConsoleHeaderMissing", ...}}`.
  3. `POST /v1/console/session` passes.
  4. Otherwise, a missing cookie or a failed `resolve` gives
     `401 {"error":{"class":"ConsoleSessionRequired","message":"sign in to the console"}}`, with
     no `WWW-Authenticate` header.
- **Routes:**
  - `POST /session` takes `{"username","password"}`.
    - Success: `200 {"username"}` and `Set-Cookie` carrying the attributes from Global
      Constraints.
    - Failure: `401 {"error":{"class":"LoginRefused","message":"wrong username or password, or the account is locked"}}`.
  - `GET /session` returns `200 {"username"}`.
  - `DELETE /session` calls `logout`, returns `204`, and expires the cookie (`Max-Age=0`).

- [ ] **Step 1: Write the failing tests** in `tests/contract/test_console_session.py`.
  - The client is built with
    `build_app(_Deployment(store_root=tmp_path, runs=InMemoryRunQueue(), console=accounts, keys=<KeyRing with an admin-scoped and a plain key>))`.
  - `accounts` is an `InMemoryConsoleAccounts` holding `alice` / `"long enough pass"`.
  - Every write sends `headers={"X-Docdoc-Console": "1"}`.

```python
def test_login_sets_an_httponly_strict_cookie(client):
    r = client.post("/v1/console/session", json={"username": "alice", "password": "long enough pass"}, headers=H)
    assert r.status_code == 200 and r.json() == {"username": "alice"}
    cookie = r.headers["set-cookie"].lower()
    assert "docdoc_console=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/v1/console" in cookie
    assert client.get("/v1/console/session").json() == {"username": "alice"}

def test_a_wrong_password_and_an_unknown_user_get_one_answer(client):
    a = client.post("/v1/console/session", json={"username": "alice", "password": "nope nope nope"}, headers=H)
    b = client.post("/v1/console/session", json={"username": "mallory", "password": "nope nope nope"}, headers=H)
    assert a.status_code == b.status_code == 401 and a.json() == b.json()

def test_a_write_without_the_console_header_is_refused(client):
    r = client.post("/v1/console/session", json={"username": "alice", "password": "long enough pass"})
    assert r.status_code == 403 and r.json()["error"]["class"] == "ConsoleHeaderMissing"

def test_a_bearer_key_never_opens_the_console_api(client):
    for key in (ADMIN_KEY, PLAIN_KEY, "garbage"):
        r = client.get("/v1/console/session", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 401 and r.json()["error"]["class"] == "ConsoleSessionRequired"

def test_a_console_session_never_opens_the_rest_of_v1(signed_in):
    assert signed_in.get("/v1/runs").status_code == 401

def test_logout_ends_the_session(signed_in):
    assert signed_in.delete("/v1/console/session", headers=H).status_code == 204
    assert signed_in.get("/v1/console/session").status_code == 401

def test_without_a_run_database_the_console_says_so(tmp_path):
    client = TestClient(build_app(_Deployment(store_root=tmp_path)))
    r = client.post("/v1/console/session", json={"username": "a", "password": "b"}, headers=H)
    assert r.status_code == 503 and r.json()["error"]["class"] == "ConsoleUnavailable"

def test_the_console_needs_a_session_even_with_tenant_auth_off(tmp_path, accounts):
    client = TestClient(build_app(_Deployment(store_root=tmp_path, runs=InMemoryRunQueue(), console=accounts)))
    assert client.get("/v1/console/session").status_code == 401

def test_the_shell_is_still_open(client):
    assert client.get("/console/").status_code in (200, 501)
```

  `signed_in` is a fixture: the client after a successful login. The TestClient keeps cookies.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/contract/test_console_session.py -v`
  Expected: FAIL. Paths 404 or 401 Bearer, and `_Deployment` rejects `console=`.
- [ ] **Step 3: Implement `api/console.py`, the `_Deployment` wiring, and the gate.** In the
  middleware, check console paths *before* the Bearer branch. Update the docstrings of
  `_CONSOLE_PREFIX` and `_require_credential_everywhere_else` to name the new exemption and
  ADR-0023 §3.
- [ ] **Step 4: Run them and confirm they pass.**
- [ ] **Step 5: Update the surface contract tests.**
  - In `test_console_adds_one_read.py`, add
    `CONSOLE_ADDED = {("/v1/console/session", "POST"), ("/v1/console/session", "GET"), ("/v1/console/session", "DELETE")}`
    to `ADDED`. Tasks 5 and 6 extend it.
  - Replace the session-words test with one asserting that every path containing a
    `SESSION_WORDS` word starts with `/v1/console/`.
  - Add the same three routes to `ROUTES` in `test_http_ui_endpoints.py`.
  Run: `uv run pytest tests/contract -v`
  Expected: PASS.
- [ ] **Step 6: Commit**

```bash
git add src/docdoc/api/console.py src/docdoc/api/app.py tests/contract
git commit -m "feat(api): console session routes behind their own gate"
```

---

### Task 5: Console run reads across tenants

**Files:**
- Modify: `src/docdoc/runs/queue.py` (Protocol) and `src/docdoc/runs/postgres.py`, adding two
  methods
- Modify: `tests/fixtures/run_queue.py` (the same two methods)
- Modify: `src/docdoc/api/console.py` (four routes)
- Modify: `src/docdoc/api/models.py` (`ConsoleRunSummary`, `ConsoleRunListResponse`)
- Modify: `src/docdoc/api/app.py`. Extract the body of `run_result` after the run is fetched into
  a module-level `_ocr_document(request: Request, tenant_id: str, run: Any) -> Any`, so that both
  routes call it.
- Modify: the two surface contract tests (add the routes)
- Test: `tests/contract/test_console_runs.py`, `tests/integration/test_run_listing.py` (one Postgres case for the new query)

**Interfaces:**
- Consumes: `Operator`, `console_router` (Task 4); `_routing_for`, `_terminal`, `_stored_result`,
  `_well_formed`, `_unknown_run`, and `_ocr_document` from `app.py`; `paging.encode/decode`.
- Produces, on the run queue (Postgres and in-memory):
  - `page_all_runs(*, tenant_id: str | None, status: str | None, before: tuple[datetime, UUID] | None, limit: int) -> tuple[Run, ...]`.
    This is `page_runs` with the tenant predicate dropped when `tenant_id is None`.
  - `owner_of(run_id: UUID) -> str | None`, which looks in `runs`, then `run_tombstones`.
- Produces, in `api/models.py`: `ConsoleRunSummary(RunSummary)`, which adds `tenant_id: str`, and
  `ConsoleRunListResponse(runs: tuple[ConsoleRunSummary, ...], next_cursor: str | None)`.
- **Routes**, which all answer `503 RunStateUnavailableError` like `/v1/runs` when there are no
  runs:
  - `GET /v1/console/runs?tenant=&status=&limit=&cursor=`
    - Validation is identical to `/v1/runs`: status 422, limit 422.
    - A cursor is encoded and decoded with `tenant_id = tenant or "*"`. `"*"` can never match
      `TENANT_PATTERN`, so a cursor is bound to the filter that produced it.
  - `GET /v1/console/runs/{run_id}`
    - `owner_of`, then `get(id, owner)`, which gives the `RunStateResponse` body plus
      `"tenant_id"`, plus `routing` when configured.
    - A tombstone gives 410 in the same body as `/v1/runs/{id}`.
    - Unknown or malformed gives `_unknown_run`.
  - `GET /v1/console/runs/{run_id}/delivery`. The body is the same as `/v1/runs/{id}/delivery`,
    looked up under the owner.
  - `GET /v1/console/runs/{run_id}/result`
    - OCR: `_ocr_document(request, owner, run)`.
    - Extraction: if succeeded, `_stored_result(store, run.processing_id, validation)` from
      `stores_for(owner)`.
    - Not succeeded: `409 {"error":"run_not_succeeded","status":...}`.

- [ ] **Step 1: Write the failing tests** in `tests/contract/test_console_runs.py`. Use the
  Task 4 `signed_in` fixture plus an `InMemoryRunQueue` seeded with runs for `acme` and `globex`,
  built the way `tests/integration/test_run_listing.py` builds `Run` objects.
  - `test_the_list_spans_tenants_and_names_each_rows_tenant`: no filter returns both tenants'
    runs, newest first, and every row has `tenant_id`.
  - `test_a_tenant_filter_returns_only_that_tenant`
  - `test_a_cursor_is_bound_to_its_tenant_filter`: page 1 with `tenant=acme&limit=1` gives a
    cursor. Replaying it with no tenant, or with `tenant=globex`, gives
    `400 InvalidCursor`.
  - `test_detail_finds_a_run_without_knowing_its_tenant`: `GET /v1/console/runs/{globex_run}` is
    200 with `tenant_id == "globex"`.
  - `test_detail_of_a_removed_run_is_410`, after `entomb` in the fixture.
  - `test_an_unknown_and_a_malformed_id_get_one_answer`
  - `test_result_of_an_unfinished_run_is_409`
  - `test_result_of_a_succeeded_ocr_run_is_its_document`: seed the store with a `Document` under
    the run's `processing_id` the way `tests/contract/test_ocr_queue.py` does. Then `pages` is in
    the body.
  - `test_the_console_run_routes_refuse_a_bearer_key`: every route above, with an admin Bearer key
    and no cookie, gives 401 `ConsoleSessionRequired`.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/contract/test_console_runs.py -v`
  Expected: FAIL with 404s.
- [ ] **Step 3: Add the queue methods**, Postgres and in-memory. In `test_run_listing.py`, add a
  Postgres case, `test_page_all_runs_spans_tenants_and_pages_exactly_once`, mirroring the existing
  SC-009 test without a tenant.
- [ ] **Step 4: Implement the models, `_ocr_document` extraction, and the routes.** Run the
  previously passing OCR result tests to confirm the extraction changed nothing:
  `uv run pytest tests/contract/test_ocr_queue.py tests/contract/test_console_runs.py -v`
  Expected: PASS.
- [ ] **Step 5: Add the four routes to `CONSOLE_ADDED` and `ROUTES`.** Then run
  `uv run pytest tests/contract -v`.
  Expected: PASS.
- [ ] **Step 6: Commit**

```bash
git add src/docdoc tests
git commit -m "feat(api): console run list, detail, delivery, result across tenants"
```

---

### Task 6: Console credentials and erasure

**Files:**
- Modify: `src/docdoc/api/console.py` (five routes)
- Modify: the two surface contract tests
- Test: `tests/contract/test_console_admin.py`

**Interfaces:**
- Consumes: `deployment.keys.issue/list_for/revoke`; `_audit(actor=..., operation=..., ...)`;
  `_erase(request, tenant_id=..., blob_id=...)`; `root_tenant()`; `TENANT_PATTERN`.
- **Routes:**
  - `GET /v1/console/credentials?tenant=` returns the same body as `GET /v1/admin/credentials`.
    An invalid tenant gives 422.
  - `POST /v1/console/credentials` takes `{"tenant_id","label"}` and returns 201 with the same
    body as the admin route.
    - The request has no scopes field.
    - Any `scopes` key in the body gives `422 {"error":"scopes_not_accepted"}`, because an
      admin-scoped key stays CLI-only (ADR-0023 §2).
  - `DELETE /v1/console/credentials/{credential_id}` returns 204, idempotently.
  - `DELETE /v1/console/tenants/{tenant_id}` calls `_erase`. The default tenant is refused with the
    same 409 the admin route gives.
  - `DELETE /v1/console/tenants/{tenant_id}/documents/{blob_id}` calls `_erase` with `blob_id`.
- Audit: `actor=f"console:{operator}"`.

- [ ] **Step 1: Write the failing tests** in `tests/contract/test_console_admin.py`, over
  `signed_in`. The deployment uses a `ChainedKeyStore` around the `FakeStore` from
  `tests/unit/test_credential_lifecycle.py`, or a minimal equivalent in the test.
  - `test_issue_returns_the_key_once_and_list_omits_it`
  - `test_issue_refuses_any_scopes_field`: `{"tenant_id":"acme","scopes":["admin"]}` gives 422.
  - `test_revoke_is_idempotent`: both calls give 204.
  - `test_erasing_the_default_tenant_is_refused`: 409 with
    `error == "default_tenant_erasure_refused"`.
  - `test_document_erasure_names_its_tenant`: `DELETE /v1/console/tenants/acme/documents/<blob>`
    is 200 and the counts are present.
  - `test_admin_actions_are_audited_with_the_operator`: `caplog` holds an event whose actor is
    `console:alice`.
  - `test_the_console_admin_routes_refuse_a_bearer_key`
  - `test_writes_need_the_console_header`
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/contract/test_console_admin.py -v`
  Expected: FAIL.
- [ ] **Step 3: Implement the routes.**
- [ ] **Step 4: Run the tests and confirm they pass.** Then add the five routes to `CONSOLE_ADDED`
  and `ROUTES` and run `uv run pytest tests/contract tests/unit -v`.
  Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src/docdoc/api/console.py tests/contract
git commit -m "feat(api): console credentials and erasure"
```

---

### Task 7: Frontend model: login, console intents, refusal handling

**Files:**
- Modify: `ui/src/transport.ts`. Drop `credential` from `Options`, and always send
  `X-Docdoc-Console: 1` plus `Content-Type: application/json` when there is a body. Update the
  docstrings, which currently describe the Bearer key.
- Rewrite: `ui/src/console/model/session.ts`
- Rewrite: `ui/src/console/model/client.ts` (intents, `ALLOWED`, `requestFor`, `allIntents`)
- Modify: `ui/src/console/model/failure.ts`. `ConsoleSessionRequired` becomes `unauthenticated`,
  and two kinds are added: `unavailable` for `ConsoleUnavailable` (503) and `refused` for
  `LoginRefused`.
- Modify: `ui/src/console/model/credentials.ts`. The issue body becomes `{tenant_id, label?}` and
  the `admin` flag is dropped.
- Test: `ui/test/console/session.test.ts`, `client.test.ts`, `failure.test.ts`, `credentials.test.ts`

**Interfaces:**
- Produces, in `session.ts`:
  - `type Session = { username: string | null; checked: boolean }`. `checked` is false until the
    first `GET /session` answers.
  - `emptySession(): Session`
  - `signedIn(session, username: string): Session`
  - `signedOut(session): Session`
  - `ready(session): boolean`, which is `username !== null`
- Delete: `IDLE_BOUND_MS`, `expired`, `touch`, `asOpen`, `withAdministrative`, `normalise`, and
  `Mode`. Expiry is now server-side.
- Produces, in `client.ts`: `Intent` =
  - `{type:"who-am-i"}`, `{type:"sign-in"}`, `{type:"sign-out"}`
  - `{type:"list-runs"; tenant?: string; status?: RunStatus; limit?: number; cursor?: string}`
  - `{type:"run-detail"; runId}`, `{type:"run-delivery"; runId}`, `{type:"run-result"; runId}`
  - `{type:"list-credentials"; tenant}`, `{type:"issue-credential"}`,
    `{type:"revoke-credential"; credentialId}`
  - `{type:"erase-tenant"; tenantId}`, `{type:"erase-document"; tenantId; blobId}`
  - Every path is under `/v1/console`, and `ALLOWED` lists exactly those shapes.
  - `PROBE_TENANT` and `resultHref` are deleted.
- Produces, in `failure.ts`: `Failure` gains `{kind:"unavailable"; message}` and
  `{kind:"refused"; message}`. `describe` returns the server's message for both.

- [ ] **Step 1: Rewrite the four test files.** Key assertions:

```ts
test("every intent addresses the console API and nothing else", () => {
  for (const intent of allIntents()) {
    const plan = requestFor(intent);
    assert.ok(plan.path.startsWith("/v1/console/"), plan.path);
    assert.ok(isAllowed(plan));
  }
});
test("sign-in is a POST with a body; who-am-i a GET; sign-out a DELETE", () => {
  assert.deepEqual(requestFor({ type: "sign-in" }), { method: "POST", path: "/v1/console/session", query: {}, hasBody: true });
  assert.equal(requestFor({ type: "who-am-i" }).method, "GET");
  assert.equal(requestFor({ type: "sign-out" }).method, "DELETE");
});
test("document erasure names its tenant in the path", () => {
  assert.equal(requestFor({ type: "erase-document", tenantId: "acme", blobId: "sha256:x" }).path,
    "/v1/console/tenants/acme/documents/sha256%3Ax");
});
test("the run list carries the tenant filter only when set", () => {
  assert.deepEqual(requestFor({ type: "list-runs" }).query, {});
  assert.deepEqual(requestFor({ type: "list-runs", tenant: "acme" }).query, { tenant: "acme" });
});
test("a refusal signs the operator out with a notice", () => {
  const f = failureOf({ ok: false, status: 401, body: { error: { class: "ConsoleSessionRequired", message: "sign in to the console" } } });
  assert.equal(f.kind, "unauthenticated");
  assert.equal(signedOut(signedIn(emptySession(), "alice")).username, null);
});
test("unavailable is not a wrong password", () => {
  const f = failureOf({ ok: false, status: 503, body: { error: { class: "ConsoleUnavailable", message: "the console needs a run-state database; …" } } });
  assert.equal(f.kind, "unavailable");
  assert.match(describe(f), /run-state database/);
});
test("issuing a credential sends no scope", () => {
  assert.deepEqual(JSON.parse(issueBody("acme", "")), { tenant_id: "acme" });
  assert.deepEqual(JSON.parse(issueBody("acme", "ci")), { tenant_id: "acme", label: "ci" });
});
```

  `issueBody(tenant: string, label: string): string` is the new export in `credentials.ts` that
  `CredentialArea` will call.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `cd ui && npm test`
  Expected: FAIL, on missing exports and old paths.
- [ ] **Step 3: Implement the model and transport changes.**
- [ ] **Step 4: Run the tests and confirm they pass.**
  Run: `cd ui && npm test && npm run typecheck`
  Expected: the tests pass. The typecheck errors only in components, which Task 8 fixes. Note
  those errors in the task report.
- [ ] **Step 5: Commit**

```bash
git add ui/src/transport.ts ui/src/console/model ui/test/console
git commit -m "feat(ui): console model speaks the session API"
```

---

### Task 8: Frontend components: login form, sign-out, and the fixes

**Files:**
- Create: `ui/src/console/components/LoginForm.tsx`, which replaces the deleted
  `CredentialPrompt.tsx`
- Modify: `ui/src/console/components/Shell.tsx`, `RunArea.tsx`, `CredentialArea.tsx`, `EraseArea.tsx`
- Modify: `ui/scripts/check-console-boundary.mjs`. Delete the `RESULT_CONTENT` rule set, the
  exemption machinery, and its output block, citing ADR-0023 §6, and keep `REVIEW_PLATFORM`
  unchanged. Update `ui/test/guards-can-fail.test.mjs` if it has a result-content fixture.
- Modify: `ui/package.json` (`description`), `docs/concepts/console.md` (sign-in, the CLI, the idle
  setting, the `Secure` cookie behind a TLS proxy needing `uvicorn --proxy-headers`, and the route
  family)

**Interfaces:**
- Consumes: Task 7's `Intent`, `Session` helpers, `Failure` kinds, and `issueBody`.
- Behaviour:
  - **`Shell`**
    - On mount it sends one `who-am-i`. A 200 gives `signedIn`, a 401 shows `LoginForm`, and a
      503 shows the `unavailable` sentence in place of the form.
    - Any `unauthenticated` failure from any area gives `signedOut` plus the notice "Your session
      ended. Sign in again."
    - The header shows the username and a **Sign out** button, which sends `sign-out` and then
      `signedOut`.
    - All three areas are always shown. The area buttons are labelled "Runs", "Credentials",
      "Erasure", capitalised.
    - The idle tick and the admin probe are removed.
  - **`LoginForm`**
    - Username: `<input autoComplete="username">`. Password:
      `<input type="password" autoComplete="current-password">`. Both sit in Astryx `Field`s.
    - A Sign in button, also triggered by submitting the form with Enter.
    - On `refused` it shows the server message, and on `unavailable` the server sentence.
  - **`RunArea`**
    - A **Tenant** text filter, applied on Enter or the Apply button rather than per keystroke.
    - A **Refresh** button and a **Tenant** column.
    - Detail shows a loading line while fetching and the `describe(failure)` text on any failure
      other than `removed` and `absent`. Today those are swallowed.
    - The result `<Link>` is replaced by a **Download result** button. It sends `run-result`,
      turns the body into
      `new Blob([JSON.stringify(body, null, 2)], {type:"application/json"})`, and clicks a
      temporary `<a download="<run_id>.json">` built on `URL.createObjectURL`, revoking it
      afterwards.
    - The button shows for any succeeded run, OCR or extraction.
  - **`CredentialArea`**
    - The administrative checkbox is removed.
    - The state variable `scope` is renamed `label`.
    - The listing loads on a **Show** button rather than on every keystroke.
    - **Revoke** asks for an inline confirmation: the row shows "Revoke this key?" with Confirm
      and Cancel buttons, and only Confirm sends the request.
  - **`EraseArea`**: document erasure gains a Tenant field, and the retype confirmation stays.

- [ ] **Step 1: Implement the components and the boundary-script change.**
- [ ] **Step 2: Run all frontend checks.**
  Run: `cd ui && npm run typecheck && npm test && npm run lint:boundaries && npm run build`
  Expected: all pass. `ui/dist` is produced.
- [ ] **Step 3: Verify in the real app** (the `run` skill).
  - Start Postgres.
  - Run `uv run docdoc migrate` and `uv run docdoc console-user add alice --password-stdin`.
  - Start `uv run uvicorn docdoc.api.app:create_app --factory`.
  - Open `http://127.0.0.1:8000/console/`.
  Check:
  - a wrong password shows the refusal sentence;
  - signing in shows Runs with a Tenant column;
  - Download result saves JSON for a succeeded run;
  - Credentials has no admin checkbox, and Revoke asks first;
  - Sign out returns to the form;
  - after `DOCDOC_CONSOLE_IDLE_SECONDS=60` and two minutes idle, the next click returns to the
    form with "Your session ended. Sign in again."
- [ ] **Step 4: Run the full Python suite once.**
  Run: `uv run pytest -q`
  Expected: PASS. Postgres-backed tests are skipped without `DOCDOC_TEST_DATABASE_URL`.
- [ ] **Step 5: Commit**

```bash
git add ui docs/concepts/console.md
git commit -m "feat(ui): sign-in console; fix result download, revoke confirm, sign-out, refresh, detail errors"
```
