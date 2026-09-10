# Limits

Four per-tenant limits, **all absent by default**. A deployment that configures
none counts nothing, refuses nothing, and behaves exactly as Milestone 9 did.

| Limit | Variable | Mechanism |
|---|---|---|
| Submissions per minute | `DOCDOC_LIMIT_SUBMISSIONS_PER_MINUTE` | fixed-window counter |
| Concurrent runs | `DOCDOC_LIMIT_CONCURRENT_RUNS` | counted from `runs` |
| Runs per period | `DOCDOC_LIMIT_RUNS_PER_PERIOD` | fixed-window counter |
| Tokens per period | `DOCDOC_LIMIT_TOKENS_PER_PERIOD` | fixed-window counter |

Per-tenant overrides live in a JSON file named by `DOCDOC_LIMITS_FILE`:

```json
{
  "default": { "submissions_per_minute": 60 },
  "tenants": { "bulk": { "submissions_per_minute": 600, "concurrent_runs": 20 } }
}
```

A tenant with no entry gets the default; an entry that omits a field inherits the
default's value for it. **Absent is not zero** — it means the limit does not
exist for that tenant. The file is read once, at startup, like the key file.

## Counting is not billing

Constitution v1.8.0 permits counting **in order to refuse** and continues to defer
metering **in order to bill**. docdoc prices nothing, invoices nothing, and emits
no billing record. A counter that says "not now" is a limit; the same counter
exported to an accounts-receivable system is metering, and adding the second
needs its own amendment.

That distinction had to be made by amendment rather than by argument. A
per-tenant token counter is metering under the plainest reading of the sentence
it amends, and a specification that reinterpreted that sentence to comply with
itself would invert the precedence the constitution's Governance section sets.

## What a refusal says, and why it says more than a `401`

```json
{ "error": "limit_exceeded", "limit": "concurrent_runs",
  "observed": 25, "allowed": 25, "retry_after_seconds": 30 }
```

`429`, with `Retry-After`. **Being over a quota is not a secret**, which is the
opposite of an authentication refusal: that one says nothing at all, deliberately,
because a different message for each cause would tell an attacker which keys are
worth guessing. A client told only "no" retries immediately and forever, so these
two refusals must not be confusable.

A refused submission **creates no run, consumes no queue position, and touches no
store.** It costs the deployment one counter read.

## The fixed window, and the burst it permits

A tenant may issue **up to twice its limit across a window boundary** — three in
the last second of one minute and three in the first second of the next, against
a limit of three.

That is documented rather than engineered away. A sliding window needs either a
row per submission, which would make the limiter the largest table in this
database, or a background decay process, which is the fifth process this
milestone does not add. And the bound that actually protects a deployment is the
worker pool size, which the burst does not touch.

`tests/unit/test_limit_windows.py` asserts the burst rather than avoiding it: a
test that let it pass silently would be a test agreeing with an implementation.

## The token budget is one submission late

Tokens are known only after a provider answers. So the budget refuses the **next**
submission and can never abort the one that produced them.

That is the safe direction and it is deliberate. A budget that could stop a run
mid-flight would discard work already paid for — the paid-and-discarded failure
that Milestone 9's whole asynchronous design existed to remove. There is no verb
in the limiter that could express it.

## Two things that do not happen

**No limit aborts, cancels, or discards a run that is already executing.**
Lowering a limit below a tenant's current usage refuses new submissions and leaves
everything in flight alone.

**No limit is held in a worker's memory.** Counters are rows and concurrency is a
query, so adding a replica does not multiply a limit — which is what an
in-process counter would do, once per replica, silently.

## Fairness is a separate thing

A limit stops one tenant consuming a deployment. **Fairness decides who goes next
when several are waiting**, and it is not configured: the claim prefers the tenant
served least in the last ten minutes, so one tenant's backlog delays another by at
most one claim per tenant with work rather than by the size of the backlog.

Past `DOCDOC_RUN_STARVATION_SECONDS` (default one hour) a run outranks everything,
including a newer urgent one from another tenant. That is what makes a
misconfigured priority ceiling a latency problem and never a liveness one.

## Priority, and the ceiling that bounds it

A client marks its own submission `ordinary` or `urgent`, bounded by a per-tenant
ceiling an operator sets. A request above the ceiling is **accepted at the
ceiling** and told what it was granted — not refused, because an operator
lowering a ceiling must not break a client that changed nothing.

A tenant cannot raise its own ceiling through any route. Letting a client choose
freely was rejected on the obvious ground: every client chooses the top of the
scale, and in a shared deployment that is self-service escalation past another
customer's queue.

The deployment-wide ceiling is `DOCDOC_PRIORITY_CEILING`, `ordinary` or `urgent`,
and it defaults to **`ordinary`** — so a deployment that configures nothing grants
nothing, and every run is claimed in creation order exactly as it was before.

Per-tenant ceilings live in the same `DOCDOC_LIMITS_FILE` the four limits use,
because a ceiling *is* a limit and a second per-tenant file would be a second
place for one customer's policy to live:

```json
{
  "tenants": {
    "bulk": { "submissions_per_minute": 600, "priority_ceiling": "urgent" }
  }
}
```

The granted priority is echoed on **every** submission response, including one
that requested none. It is the only way a client can learn what it was granted,
because the ceiling is deliberately readable through no route.
