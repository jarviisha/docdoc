# Operator files for the development composition

Mounted read-only at `/secrets` in `api` and `worker`. Nothing here is committed —
the `.gitignore` beside this file keeps it that way.

**This directory exists because there was no way to get an operator's file into a
container.** Three of `quickstart.md`'s scenarios need one — a key file to turn
authentication on, a webhook signing secret, a routing policy — and the composition
mounted only `schemas/` and the echo fixtures. The documented alternative was
`docker cp` followed by `docker compose up -d`, which **cannot work**: changing an
environment variable recreates the container, and the copied file goes with it.
The API then refuses to start, correctly, on an unreadable key file.

## What goes here

```bash
# authentication (scenario 1)
BOOT=ddk_change_me
printf '{"keys":[{"sha256":"%s","tenant_id":"ops"}]}\n' \
  "$(printf '%s' "$BOOT" | sha256sum | cut -d' ' -f1)" \
  > packaging/docker/secrets/keys.json

# webhook signing (scenario 4)
printf '{"secrets":["whsec_change_me"]}\n' > packaging/docker/secrets/webhook-secrets.json

# routing (scenario 5)
cp examples/routing/default.json packaging/docker/secrets/routing.json
```

Then point the settings at `/secrets/...`:

```bash
DOCDOC_API_KEYS_FILE=/secrets/keys.json \
DOCDOC_DELIVERY_SECRETS_FILE=/secrets/webhook-secrets.json \
DOCDOC_ROUTING_POLICY=/secrets/routing.json \
  docker compose -f packaging/docker/compose.yml up -d api worker
```

Because the directory is a bind mount, the files survive the container being
recreated — which is the whole point.
