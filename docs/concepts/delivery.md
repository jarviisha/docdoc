# Webhook delivery

A client that submits an asynchronous run has to find out when it finishes.
Polling works and always will; this is the option that lets a client stop.

**It is off until configured**, and a deployment that registers no callbacks
performs no outbound request at all — it opens no socket to discover there was
nothing to send.

## What a receiver gets

One `POST` per terminal run, carrying identities and states:

```json
{ "delivery_id": "b41e…", "run_id": "0f8b…", "status": "succeeded",
  "processing_id": "9ab2…" }
```

A failed run carries `failed_stage` and `error_class` instead of
`processing_id`. Fields that do not apply are **absent**, not null.

It carries no document text, no extracted value, no prompt body, no credential,
and no provider message. That is the rule every observer in docdoc follows,
applied to the one of them that leaves the deployment.

**It is a notification, not a result.** The result is retrieved through
`GET /v1/jobs/{processing_id}/result`, exactly as it always was.

## Signing

```text
X-Docdoc-Signature: t=1788000000,v1=<hex>
```

`v1` is `HMAC-SHA256(secret, f"{t}.{body}")`. Standard library only; no
dependency was added for any of this.

**The timestamp is inside the signed material.** Signing the body alone produces
a value that stays valid for as long as the secret does, so a captured delivery
could be replayed at any point in the future and would verify. With `t` signed, a
receiver can reject what is too old — and moving `t` invalidates the signature.

To verify:

```python
import hashlib, hmac, time

parts = dict(p.split("=", 1) for p in header.split(","))
signed = f"{parts['t']}.".encode() + body           # the raw bytes, not re-serialised
expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

assert hmac.compare_digest(expected, parts["v1"])
assert abs(time.time() - int(parts["t"])) < 300      # your freshness window
```

Verify against the **raw body**. Re-serialising the JSON first will change the
bytes and the signature will not match.

`examples/receive_webhook.py` is that, runnable.

## The secret is configuration, never a row

`callbacks` stores `sha256(secret)` — enough to tell two registrations apart and
not enough to sign with. The secret itself lives in a file the operator
configures, and a delivery is signed with the configured secret whose digest
matches the registration.

So there is no route that returns a signing secret, because no process serving
routes holds one. Registration with a secret the deployment has not configured is
**refused**: a callback that registered successfully and could never be delivered
would report a configuration mistake as six failed attempts per run.

## The destination policy

A callback URL is chosen by a caller and fetched by the deployment, from inside
whatever network the deployment sits in. That is server-side request forgery
waiting to happen, so:

1. Resolve the host.
2. Refuse if **any** resolved address is loopback, link-local, private,
   multicast, reserved, or unspecified. Every address, not the first — a host
   resolving to one public and one private address is the attack.
3. Connect **to the validated address**, presenting the original hostname in
   `Host` and in TLS SNI. The certificate is still verified against the
   registered name.
4. **Do not follow redirects.** A public URL answering `302 →
   169.254.169.254` is the other half of the same attack. A `3xx` is recorded as
   the status it is and counts as a failed attempt.

Steps 1 and 2 run at registration **and again before every attempt**. Only the
second closes DNS rebinding: the name that resolved publicly an hour ago resolves
to the metadata service now. The first exists so an operator finds out about a
refused destination while they are looking at the response.

A refusal is `422` naming the **class** of refusal and **not the addresses it
resolved to** — a body that reported them would make the route a name resolver
for a caller who has one credential and no other way to ask.

`https` is required. `DOCDOC_DELIVERY_ALLOW_PRIVATE` relaxes both the scheme and
the address policy together, for the one case that needs it: a receiver on the
same host or the same private network.

## At-least-once, and one per run

**`delivery_id` is stable across every retry.** It is the delivery row's primary
key, allocated once. It is the value to deduplicate on: an id regenerated per
attempt would turn at-least-once *delivery* into at-least-once *processing* on
the receiver's side, which is the one thing at-least-once is meant to let them
avoid.

**One delivery per run**, by a `UNIQUE (run_id)` constraint rather than by worker
discipline. Retries are attempts on one row, so two deliveries for one run cannot
overlap because there are never two.

**The delivery is enqueued in the same transaction that records the terminal
state**, so there is no window in which a run is finished and its notification is
lost.

Retries back off exponentially and come to rest at `failed` after the attempt
limit. The state is readable, so a client can see that it stopped and why:

```text
GET /v1/runs/{run_id}/delivery
{ "delivery_id": "b41e…", "state": "failed", "attempts": 6,
  "last_status": 503, "last_error": "HTTPError", "next_attempt_at": null }
```

`last_error` is a **class name**, never the receiver's response body. A body is
somebody else's content, and it can quote the request that produced it.

## A failing receiver costs nothing else

A broken or slow destination changes no run state, blocks no worker, and does not
delay another tenant's deliveries beyond its own timeout. The run stays
`succeeded`; whether anybody was told is a separate fact.

**Polling remains the record.** A lost delivery loses no information — the
terminal state, the `processing_id`, and the failure class are all still readable
from `GET /v1/runs/{run_id}` afterwards.

## When it runs

The worker attempts due deliveries between claims, before the retention sweep and
sharing the same budget. The ordering is deliberate: a delivery is a promise made
to somebody outside the deployment, and a sweep is idempotent housekeeping that
can wait for the next tick without anybody noticing.

Bounded **between** attempts and never within one. A delivery has its own timeout
and cutting it mid-flight would produce an attempt nobody can classify, so the
worst case a claim waits is one tick budget plus one delivery timeout.

**A deployment running no worker delivers nothing.**

## Limits

- Delivery is **at-least-once**, not exactly-once. Deduplicate on `delivery_id`.
- One delivery per run, so a run that is redelivered and re-finished still
  notifies once.
- A `3xx` is not a success and is not followed.
- The signing secret must be configured before a callback can be registered.
- Registration is per tenant; a callback belongs to the tenant that created it,
  and another tenant naming its identifier gets `404`.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `DOCDOC_DELIVERY_SECRETS_FILE` | the signing secrets; **unset means nothing is delivered** | unset |
| `DOCDOC_DELIVERY_MAX_ATTEMPTS` | attempts before a delivery comes to rest at `failed` | 6 |
| `DOCDOC_DELIVERY_TIMEOUT_SECONDS` | how long one attempt may take | 10 |
| `DOCDOC_DELIVERY_BACKOFF_SECONDS` | the first retry's delay, doubled thereafter | 30 |
| `DOCDOC_MAINTENANCE_DELIVERY_BATCH` | deliveries attempted per tick | 32 |
| `DOCDOC_DELIVERY_ALLOW_PRIVATE` | permits `http` and private addresses | off |

None has a command-line flag. Delivery is configured by the deployment and
performed by whichever worker ticks, so every one of these must be the same in
every process — and `DOCDOC_DELIVERY_ALLOW_PRIVATE` turns off a guard against
server-side request forgery, which must not be reachable from a command line
somebody is improvising on.

## See also

- [ADR-0018 — webhook delivery semantics](../adr/0018-webhook-delivery-semantics.md)
- `examples/receive_webhook.py` — a receiver that verifies
- [runs](runs.md) — polling, which remains the record
