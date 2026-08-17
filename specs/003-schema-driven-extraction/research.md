# Phase 0 Research: Schema-Driven Extraction

Fifteen decisions the spec left to planning, each recorded as Decision / Rationale / Alternatives. Two
of them (R4, R5) surface a tension the spec did not anticipate rather than settle it quietly.

Constitution v1.2.0; ADR-0002 (canonical form), ADR-0003 (artifact chain), ADR-0004 (confidence),
ADR-0008 (schema evolution).

## R1 — The one LLM adapter: Anthropic Claude, shipped as an opt-in extra

**Decision.** `docdoc[anthropic]` → the official `anthropic` SDK, confined to
`src/docdoc/extraction/adapters/anthropic_messages.py`. The base install stays `pydantic` alone.

**Rationale.** Three properties decide it for a grounding-first engine:

1. **Server-enforced structured output is generally available** (R2), so response conformance is a
   provider guarantee rather than a parsing exercise.
2. **A 1M-token context window** on the current models, which is what makes the one-window decision of
   the spec's clarification survive contact with real documents rather than failing on page 30.
3. **Prompt caching with a documented prefix-match rule** (R15), which turns the per-schema prompt into
   a cached prefix — the difference between paying for the instructions once per document and once per
   schema.

**Alternatives.** The constitution's packaging line names `docdoc[openai]` — but as an illustrative
example of *an extra*, in a sentence about packaging, not as a sanctioned provider choice; the stack
section says only "one LLM adapter". Choosing another provider would be a configuration and one-module
change under Principle IV, which is the entire point of the boundary. A local or self-hosted model is
deferred by the same MVP discipline that deferred a local OCR engine in ADR-0001.

**Consequence to accept.** Naming a provider in the plan is a commitment the spec deliberately avoided.
It is recorded in plan.md as a design decision so a reviewer sees it here rather than discovering it in
an import.

## R2 — Structured output via the provider's response-format constraint, not prose parsing

**Decision.** The request carries a JSON Schema derived from the docdoc schema, and the provider
constrains its response to it. Extraction never parses prose and never regex-extracts JSON out of a
text blob.

**Rationale.** FR-011 requires that conformance be checkable mechanically. A provider-enforced response
shape moves the guarantee from "our parser coped" to "the response could not have had another shape",
which is the difference between an `ExtractionError` that means "the model misunderstood" and one that
means "our parser has a bug".

**Alternatives.** Tool-use with a strict input schema is the other server-enforced route and is
equivalent in strength; it is rejected only because a tool call models *an action to take* and
extraction is not taking one. Assistant-turn prefill — the classic JSON-forcing trick — is rejected
outright: it returns an error on every current model of the chosen provider.

## R3 — The response shape is a *projection* of the schema, and the two are hashed separately

**Decision.** Two artifacts derive from one schema:

| Artifact | Contains | Purpose |
|---|---|---|
| `schema_hash` | The **whole** schema — fields, types, cardinality, **constraints**, descriptions | Identity and cache invalidation, per ADR-0008 |
| The requested response shape | Only what the provider can enforce — types, cardinality, `enum`, `const`, string formats | Sent on the wire; folded into `options_hash` per ADR-0008 |

**Rationale.** This is not a design preference; it is what the provider supports. Its structured-output
schema subset **cannot express** numeric bounds (`minimum`, `maximum`, `multipleOf`), string length
bounds (`minLength`, `maxLength`), complex array constraints, or **recursive schemas**.

Three spec decisions turn out to be forced rather than chosen, which is worth recording because each
looked like a judgment call at clarification time:

- **FR-006** — extraction checks shape and type parseability, and Milestone 5 enforces constraints. The
  provider cannot enforce a numeric range even if asked, so any other split would have been fiction.
- **FR-048** — repetition bounded to one level. Recursive schemas are not expressible at all, so the
  bound is the provider's, not ours; ours is merely stricter and checked at registration.
- **FR-008** — undeclared fields discarded. The projection sets `additionalProperties: false`, so an
  undeclared field should be impossible; FR-008 stays as a defence against the case where it happens
  anyway.

**Consequence.** A schema author can write a constraint that changes `schema_hash` — and therefore
invalidates the extraction cache — while changing nothing about what is sent to the model. That is
correct under ADR-0008 (the constraint changes what a *result* means downstream) and must be documented,
because it looks like a spurious cache miss.

## R4 — Decoding parameters do not exist on the chosen provider, and ADR-0003 says they do

**Decision.** The extract stage's `options_hash` folds `model_id`, `model_version`, `max_tokens`, the
reasoning-effort level, and the thinking mode — **not** temperature, top-p, or a seed. This **refines
ADR-0003's `Extract` row**, which lists "decoding params (temperature, top_p, seed, max_tokens)".

**Rationale.** On the chosen provider's current models, `temperature`, `top_p`, and `top_k` are removed
from the API and a request carrying any of them is rejected outright. There is no seed parameter. The
knobs that *do* affect a result are `max_tokens`, the effort level, and whether thinking is enabled.
ADR-0003 was written against the reference design's assumptions; folding a parameter that cannot exist
would be dead code, and omitting the ones that do exist would be the exact stale-cache bug ADR-0003
exists to prevent.

**Consequence, and it strengthens the spec.** FR-037 says the system must not claim byte-identical
results across runs. With no temperature and no seed there is not even a nominal determinism knob to
misrepresent, so the honest position the spec already took is the only available one.

**Not resolved here.** Whether ADR-0003 should be superseded to generalise its Extract row is a
decision for an ADR, not a plan. This plan follows ADR-0003's *rule* — fold every input that can change
the result — and records that the row's parenthetical is provider-specific.

## R5 — The input-budget guard is a local over-estimate, because measuring it exactly means transmitting

**Decision.** Two tiers:

1. **Pre-flight guard (local, no network).** A conservative character-to-token lower bound over the
   document text plus the rendered prompt. Deliberately over-estimates. Trips → `ExtractionError`
   naming the document, the bound, and the estimate, before anything is transmitted.
2. **The provider's own limit.** If the guard passes and the provider still rejects the request as too
   long, that failure is mapped to the same typed error, so a caller sees one condition rather than two.

**Rationale.** FR-041 requires that a request destined to fail transmits nothing. The provider's own
token-counting endpoint is exact — and is an API call that **transmits the document text to answer**.
Using it as the pre-flight check would transmit precisely what FR-041 exists to avoid, and would add a
round trip to every extraction.

**Alternatives.** Calling the token-counting endpoint and accepting the transmission: rejected on
FR-041. A third-party tokenizer such as `tiktoken`: rejected twice over — it is the wrong provider's
tokenizer (it under-counts by 15–20% on ordinary text and much more on code), and it would put a
dependency in the base install to produce a wrong answer.

**Consequence to accept, stated plainly.** The guard is an over-estimate, so a document that would
actually have fitted can be refused. That is the correct direction to be wrong in — refusing a document
the caller can narrow with `slice` beats transmitting one that will be rejected — but it is a real
limitation, the ratio is configurable, and it must be in the docs rather than discovered.

## R6 — Schema files are JSON, because the canonical form already exists in the kernel

**Decision.** Schemas are JSON documents. `schema_hash` is `sha256` over
`docdoc.kernel.identity.canonical_json` applied to the parsed schema.

**Rationale.** FR-013 requires the canonical form to be the one ADR-0002 already defines rather than a
second convention. The kernel already implements it, already rejects what cannot be canonically encoded
(non-finite floats, non-string keys), and already carries `IDENTITY_SCHEMA_VERSION` for the day the
derivation changes. Choosing JSON makes "the same rule" literally the same function call.

**Alternatives.** TOML parses from the standard library on the supported Python versions, but its value
model would need its own canonicalisation convention before it could be hashed — reintroducing exactly
what FR-013 forbids. YAML would add a base dependency, which Principle I forbids.

## R7 — The projection is versioned, like the text-layer rule

**Decision.** The schema→response-shape projection carries an identifier, `response-shape@1`, recorded
in every result and folded into `options_hash`.

**Rationale.** The projection is code that changes what the model is asked for. A change to it changes
results for an unchanged schema, which is the same class of thing as a change to the text-layer rule —
and Milestone 2 solved that by versioning the rule as `text-layer@1` and recording it. Reusing the idiom
means a reader who understands ingest provenance already understands this.

## R8 — Prompts are versioned data keyed to a schema identity

**Decision.** One prompt template per schema identity, stored as data beside the schema, hashed with the
same canonical rule, recorded as `prompt_hash`.

**Rationale.** FR-020. A prompt in code is a document-type-specific code path wearing a disguise, which
Principle VI forbids by name.

**Note.** Prompt text and schema field descriptions both steer the model, and both are hashed —
`prompt_hash` and `schema_hash` respectively. Neither subsumes the other and both are folded; there is no
double-counting problem because they are distinct inputs.

## R9 — Errors and transport settings are reused from ingest, not redefined and not promoted

**Decision.** `docdoc.extraction` imports `ProviderError` and `TransportSettings` from `docdoc.ingest`.
`ExtractionError` and `SchemaError` are new, rooted at the existing `DocdocError`. **No kernel change.**

**Rationale.** Principle X's order is `api > pipeline > extraction > transform > ingest > kernel`, so
`extraction → ingest` is a legal dependency, and it is added to the `import-linter` layers contract in
the same change. The constitution's error model is one flat list, which reads as one taxonomy; a second
class also called `ProviderError` in a second module is how a taxonomy becomes two. `TransportSettings`
already models exactly the attempt limit, backoff, jitter, per-attempt timeout, and overall deadline that
FR-026 requires, and is already deliberately excluded from identity — which is FR-027, already true by
construction rather than by discipline.

**Alternatives.** Promoting both to the kernel would give one home to vocabulary neither layer owns, and
Milestone 2 set a precedent for additive kernel changes from a later milestone. Rejected: a transport
knob has no business in an IR whose purity is Principle I, and there is no present-tense need
(Principle XI). Duplicating either type is rejected outright — two retry policies drift.

**Consequence.** `docdoc.extraction` depends on `docdoc.ingest` even when nothing is parsed. That costs
nothing at install time (ingest's base surface pulls no provider SDK) and is visible in the layers
contract.

## R10 — Response conformance is checked with `pydantic`, from a model built once per schema

**Decision.** At registration, each schema is compiled to a `pydantic` model via `create_model` and
cached under its identity. Response validation runs against that model; a failure becomes an
`ExtractionError` carrying the field path.

**Rationale.** `pydantic` is the one permitted base dependency and the kernel already uses it, so the
check costs no new dependency. Compiling once per schema rather than once per extraction is what keeps
SC-021's budget reachable. Field-addressable failures are what FR-007 requires in order to name what was
wrong instead of saying "malformed".

## R11 — Three test tiers, and the deterministic adapter is a deliverable

**Decision.** The same three tiers Milestone 2 settled on:

| Tier | Runs | Needs |
|---|---|---|
| Unit, property, contract | Always | Nothing — the in-repo `echo` adapter |
| Adapter mapping | Always | Recorded, scrubbed provider responses committed as fixtures |
| Live | `-m provider` | Credentials, network, money |

**Rationale.** FR-044 and SC-019. The in-repo adapter satisfies the same contract as the real one and
returns fixed responses keyed by `(document_id, schema identity)`, so US1 through US4 are all
demonstrable with no credentials. Recorded responses are what keep the mapping code — which produces
every real result — under test in CI, exactly the argument Milestone 2's Complexity Tracking made.

## R12 — Retry classification is a mapping table, and refusal is not an exception

**Decision.** Transient (retried, up to the `TransportSettings` limit): connection failure, timeout,
rate limit, server error, overloaded. Permanent (first attempt is final): rejected credential, malformed
request, request too large, unknown model, **and a content refusal**.

**Rationale.** FR-025. The trap is that a content refusal arrives as a **successful HTTP 200** whose
`stop_reason` is `refusal` — not as an exception — so code that reads the response content
unconditionally treats a refusal as an answer. The adapter therefore branches on `stop_reason` before
touching content, and a refusal becomes a typed permanent failure carrying the provider's stated
category.

`stop_reason: max_tokens` is the other stop-reason branch that matters: it means a structurally
incomplete answer, which is the spec's "model stops mid-response" edge case, and it maps to the
output-budget error of FR-030 rather than to a retry.

## R13 — Model choice is configuration, with a recorded default

**Decision.** Default `claude-opus-5`; the model id, its version, `max_tokens`, and the effort level are
configuration, recorded in every result. Reference list prices at time of writing, per million tokens:

| Model | Input | Output | Context |
|---|---|---|---|
| `claude-opus-5` (default) | $5 | $25 | 1M |
| `claude-sonnet-5` | $3 | $15 | 1M |
| `claude-haiku-4-5` | $1 | $5 | 200K |

**Rationale.** Extraction accuracy is what Milestone 6 will measure and what every grounding guarantee
inherits, so the default is the most capable model and cost reduction is an explicit, recorded choice
rather than a silent one. Recording the model *and its version* is FR-033; without the version a result
is unexplainable after a model update.

**Note on effort.** The reasoning-effort level materially changes both quality and spend and is folded
into identity (R4). Its default is a tuning decision to be fixed against the sample set during
implementation — the same shape as Milestone 2's text-layer thresholds, and for the same reason: a
number guessed before measurement is worse than a task that says to measure it.

## R14 — Thinking is on by default, and it shares the output budget

**Decision.** Thinking mode is left at the provider's default (on, adaptive) and is folded into
`options_hash`. `max_tokens` is sized with headroom for it.

**Rationale.** On the chosen provider's current default model, `max_tokens` caps thinking **plus**
response text together. A budget sized for the expected JSON alone truncates mid-answer, which surfaces
as `stop_reason: max_tokens` — a real result silently lost to a configuration error. The reasoning
content itself is never requested: it is not needed, and asking a model to reproduce its reasoning is
itself a refusal trigger on the chosen provider.

## R15 — Prompt order is chosen for the cache: instructions first, document last

**Decision.** The request is assembled stable-to-volatile — response shape, then the schema's
instructions and field descriptions, then the document text last — with the cache breakpoint at the end
of the per-schema prefix.

**Rationale.** The provider's prompt cache is a **prefix match**: any byte change invalidates everything
after it. Everything derived from `schema@version` is identical across every document extracted against
that schema, and the document is the only volatile part. Putting the document last makes the per-schema
prefix a cache hit for every document after the first, at roughly a tenth of the input price. Putting it
first — the natural reading order — would make every extraction a full-price cold write.

**Consequence.** This is a correctness-adjacent constraint, not just a cost one: the assembly order is
load-bearing and needs a test that fails if a future change interpolates anything volatile ahead of the
prefix. Nothing per-request — no timestamp, no document id, no request id — may appear before the
breakpoint.
