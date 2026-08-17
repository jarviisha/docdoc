# Phase 0 Research: Schema-Driven Extraction

Fifteen decisions the spec left to planning, each recorded as Decision / Rationale / Alternatives. R5
surfaces a tension the spec did not anticipate rather than settling it quietly.

**R1, R3, R4, R12, R13, R14, and R15 were rewritten after the provider changed from Anthropic to Google
Gemini.** Each records what the earlier revision claimed and why it was wrong, rather than being quietly
corrected — two of those claims dressed a choice as a technical constraint, and that is the kind of error
that retires a decision from review.

Constitution v1.2.0; ADR-0002 (canonical form), ADR-0003 (artifact chain), ADR-0004 (confidence),
ADR-0008 (schema evolution).

## R1 — The one LLM adapter: Google Gemini, shipped as an opt-in extra

**Decision.** `docdoc[google]` → the unified `google-genai` SDK (not the legacy
`google-generativeai`), confined to `src/docdoc/extraction/adapters/gemini.py`. The base install
stays `pydantic` alone.

**Rationale, stated honestly.** At this layer the choice is close to arbitrary, and that is not a
weakness — it is exactly what Principle IV is designed to make true. Any of the major providers
supports server-enforced structured output, a context window large enough for the one-window decision,
and prefix-oriented caching. Adding a second adapter is a new module of roughly 150 lines; the engine
does not change. So the decision was made on grounds that have nothing to do with model quality:

1. The project owner chose it.
2. The `ModelAdapter` protocol and its contract suite already exist and run against two independent
   implementations, so the first adapter is not a lock-in.

**This entry previously said something weaker, and the correction matters.** An earlier revision chose
Anthropic Claude and justified it with "three concrete properties": generally-available server-enforced
structured output, a 1M-token context window, and documented prefix-match prompt caching. None of the
three is a differentiator — OpenAI has had strict structured outputs since 2024, Gemini has had a
1M-token window for longer, and every major provider caches prefixes. Dressing an arbitrary choice as a
technical finding made it sound better founded than it was.

That revision was also written by an Anthropic model recommending Anthropic, and it did not say so.
A conflict of interest that is not disclosed at the point of decision is not disclosed at all.

**Alternatives.** The constitution's packaging line names `docdoc[openai]` as an example of *an extra*,
in a sentence about packaging; the stack section says only "one LLM adapter", so it does not bind.
A multi-provider aggregator (LiteLLM) was considered and rejected: it would make the enforcement level
of the response shape depend on the model string, with docdoc unable to tell which level it got —
turning FR-011's "the response could not have had another shape" back into "our parser coped". It would
also normalise decoding parameters across providers, so what provenance records as sent may not be what
the provider received. A local or self-hosted model stays deferred by the same MVP discipline that
deferred a local OCR engine in ADR-0001.

**Consequence to accept.** Naming a provider in the plan is a commitment the spec deliberately avoided.
It is recorded in plan.md as a design decision so a reviewer meets it there rather than in an import.

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
| The requested response shape | Types, cardinality, `enum`, string formats, `additionalProperties: false` | Sent on the wire; folded into `options_hash` |

**What the provider can and cannot enforce.** Gemini's structured-output subset is wider than the one
this entry was first written against:

| Keyword | Gemini |
|---|---|
| `minimum`, `maximum` | **Supported** |
| `minItems`, `maxItems`, `prefixItems` | **Supported** |
| `$ref` — including recursion | **Supported** |
| `enum`, `format`, `anyOf`, `additionalProperties`, `title`, `description` | Supported |
| `minLength`, `maxLength`, `pattern` | Not supported |

Only the last row is genuinely unenforceable. **Everything else the projection drops, it drops by
choice.**

**The choice, and why.** Numeric and array bounds are *not* sent, even though the provider would
enforce them. Principle VII makes validation a separate stage with its own field-addressable output;
pushing a bound onto the wire turns violating it into the provider's extraction failure rather than a
located validation failure, and makes that behaviour change when the provider changes. Milestone 5
enforces every constraint, uniformly, wherever the value came from.

**An earlier revision of this entry got this backwards, and it is worth recording how.** It claimed
three spec decisions were *forced* by the provider rather than chosen:

- **FR-006** (constraints declared here, enforced by Milestone 5) — claimed forced because "the provider
  cannot enforce a numeric range even if asked". **False.** Gemini enforces `minimum`/`maximum`. FR-006
  is a principled choice under Principle VII, and the paragraph above is the reasoning it always needed.
- **FR-048** (repetition bounded to one level) — claimed forced because "recursive schemas are not
  expressible at all". **False.** Gemini supports `$ref` recursion. The bound is entirely ours: an MVP
  scope decision about how much of the schema→shape→conformance→result path to make recursive, refused
  at registration rather than at first use.
- **FR-008** (undeclared fields discarded) — this one stands. The projection sets
  `additionalProperties: false`, so an undeclared field should be impossible; FR-008 remains as a
  defence against it happening anyway.

Calling a chosen constraint "forced" is a specific kind of error: it retires a decision from review.
Nobody re-examines a limit imposed from outside.

**Consequence.** A schema author can write a constraint that changes `schema_hash` — and therefore
invalidates the extraction cache — while changing nothing about what is sent to the model. That is
correct under ADR-0008 (the constraint changes what a *result* means downstream) and must be documented,
because it looks like a spurious cache miss.

## R4 — The decoding parameters ADR-0003 names all exist, so the row is followed literally

**Decision.** The extract stage's `options_hash` folds `model_id`, `model_version`,
`max_output_tokens`, `temperature`, `top_p`, `top_k`, `seed`, and `thinking_budget`.

**Rationale.** ADR-0003's `Extract` row lists "decoding params (temperature, top_p, seed, max_tokens)",
and on Gemini every one of them exists in `GenerateContentConfig`. The row is therefore *followed*, not
refined. `top_k` and `thinking_budget` are folded in addition, because the ADR's list is a minimum and
both change the answer.

`temperature` defaults to `0.0` rather than the provider's own default: extraction wants the least
variance on offer, and a default that differs from the provider's must be recorded — which folding it
achieves.

A `seed` is folded even though the provider treats it as best-effort. Two runs under one seed may still
differ; two runs under *different* seeds are a different request. An input that can change a result
must reach the identity whether or not it fully determines it.

**An earlier revision claimed the opposite, and presented it as the plan's headline finding.** It said
the row "lists decoding parameters that do not exist on the chosen provider" and that folding them
would be dead code — true of Anthropic's current models, which reject `temperature`/`top_p`/`top_k` and
have no seed, and false of the provider actually chosen. The finding was provider-specific and was
written as though it were about ADR-0003. `tests/unit/test_artifact_identity.py` encoded the inverted
assertion and failed when the provider changed, which is what that test was for.

**Consequence for FR-037.** The spec declines to claim byte-identical results across repeated model
calls, and that still holds — but the reason is weaker than it was. There *is* a nominal determinism
knob now. The honest statement is that `seed` plus `temperature=0.0` reduces variance while the provider
guarantees no bit-exactness, so the claim is still unavailable; it is no longer unavailable for want of
a parameter.

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

**Measured at T079, and the guess was wrong in the dangerous direction.** The first values here —
2.5 characters per token, 1.15 margin — were written before any call. Against the provider's own
`count_tokens`:

| Content | chars/token |
|---|---|
| English prose | 5.13 |
| Arabic | 1.63 |
| Vietnamese with diacritics | 1.62 |
| base64-like | 1.50 |
| Dense tabular invoice text | 1.27 |
| CJK | 1.18 |
| Numeric tables, emoji | **1.10** (the floor) |

At 2.5 the guard **under-estimated dense tabular invoice text by 1.72×** — exactly the content this
engine reads, and exactly the direction it must never be wrong in. It would have let an over-budget
document through to be transmitted, which is the failure it exists to prevent. `CHARS_PER_TOKEN` is now
**1.20**, below the 1.26 that the measured floor and the margin allow.

**Consequence to accept, now quantified.** A single linear ratio cannot serve both 1.10 and 5.13. Tuned
to the dense floor, English prose is over-estimated about 5×, so roughly 215k characters are refused
against a 200k-token budget where the true cost is about 42k tokens. That is a real usability cost, and
the escape hatch — `Document.slice` — is friction.

The alternative remains the provider's exact `count_tokens`, which transmits the document to answer. So
the trade is deliberate: refuse some documents that would have fitted rather than transmit any that will
not. Revisiting it means reopening FR-041, not loosening the constant.

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

## R12 — Retry classification is a mapping table, and a refusal is a stop reason

**Decision.** Transient (retried, up to the `TransportSettings` limit): connection failure, timeout,
rate limit, server error, resource exhausted. Permanent (first attempt is final): rejected credential,
malformed request, request too large, unknown model, **and every refusal category**.

**Rationale.** FR-025. The trap is that a refusal arrives as a **successful HTTP response**, not as an
exception, so code that reads the candidate's content unconditionally reports a refusal as an answer.
The adapter therefore branches on the stop reason before touching content.

Gemini has more refusal branches than one, and they mean different things:

| Signal | Meaning | Handling |
|---|---|---|
| `finishReason: SAFETY` | Output blocked on safety grounds | Permanent refusal; `safetyRatings` carried through verbatim |
| `finishReason: PROHIBITED_CONTENT` | Output blocked as prohibited | Permanent refusal |
| `finishReason: BLOCKLIST` | Output contained a blocklisted term | Permanent refusal |
| `finishReason: RECITATION` | Stopped because output resembled copyrighted material | Permanent, and **not a safety refusal** — a document that legitimately quotes a standard form can trip it |
| `promptFeedback.blockReason` | The *prompt* was blocked before generation | Permanent; distinct from an output block, and the only one where no candidate exists at all |
| `finishReason: MAX_TOKENS` | Structurally incomplete answer | Output-budget `ExtractionError` (FR-030), not a retry |

`RECITATION` is the one worth naming separately. It is not misconduct and it is not transient: an
invoice quoting standard terms can hit it, and retrying will hit it again. Reporting it as a safety
refusal would send the caller looking for the wrong problem.

**An earlier revision described a single `refusal` stop reason**, which is Anthropic's shape. Gemini
splits it four ways plus a prompt-level block, and collapsing them would discard the distinction between
"we will not answer this" and "the answer looked like a quotation".

## R13 — Model choice is configuration, with a recorded default

**Decision.** The model id is configuration and is recorded, with its version, in every result
(FR-033). The default is the current Gemini Pro-tier model; the exact id is fixed at T053 against the
live API rather than written from memory here, because a model id guessed in a research note is a
plausible-looking string that fails at the first call.

**Rationale.** Extraction accuracy is what Milestone 6 measures and what every grounding guarantee
inherits, so the default is the most capable tier and cost reduction is an explicit, recorded choice
rather than a silent one. Recording the model *and its version* is what makes a result explainable
after a model update.

**Measured at T059. The first default did not exist for new accounts.** `DEFAULT_MODEL` was written as
`gemini-2.5-pro` without a call. The API returns **404: no longer available to new users** — while
`models.list()` still reports it. Listing a model and being able to call it are different facts, and
only a call distinguishes them. The default is now **`gemini-3.5-flash`**, verified by calling it.

One committed invoice fixture, one call per row:

| Model | `thinking_budget` | in | out | reasoning | latency |
|---|---|---|---|---|---|
| `gemini-3.5-flash` | default | 633 | 578 | 1,288 | 7.7 s |
| `gemini-3.5-flash` | `0` | 633 | 362 | 0 | **2.7 s** |
| `gemini-3.5-flash-lite` | default | 633 | 568 | 0 | 2.5 s |
| `gemini-3.6-flash` | default | 633 | 440 | 911 | 15.9 s |

Four things this settles, and one it does not:

1. **Reasoning dominates the answer.** 1,288 reasoning tokens against 578 of output — 2.2×. R14's
   concern that a `max_output_tokens` sized for the JSON gets eaten is not theoretical.
2. **Reasoning bought nothing here.** Field accuracy was identical with and without it, at a third of
   the latency. On *this* fixture; a harder document may differ, which is why the default stays at the
   provider's automatic setting rather than being switched off globally.
3. **`thinking_budget=0` is not portable.** Accepted by `gemini-3.5-flash`, rejected with a 400 by
   `gemini-3.5-flash-lite` and `gemini-3.6-flash`. An adapter cannot assume the option exists.
4. **No pro tier was reachable.** `gemini-3.1-pro-preview` and `gemini-pro-latest` both return 429 on
   this account. So the tier comparison this task was meant to make is **incomplete**, and the flash
   default is chosen from what could be called rather than from what is best.

**Still open.** One expected field differed on every tier. The likeliest explanation is that the
*expectation* was wrong rather than the model — the document prints `BEISPIEL GMBH` and the check wanted
`Beispiel GmbH`, and returning the printed form is what the prompt asks for. Unconfirmed: the free-tier
quota (20 requests/day) was exhausted by these measurements. Reference prices stay deliberately absent —
a price table reads as authoritative and goes stale silently.

## R14 — Reasoning shares the output budget, and the failure is documented

**Decision.** `thinking_budget` is left at the provider's automatic setting by default and folded into
`options_hash`. `max_output_tokens` is sized with headroom for reasoning.

**Rationale.** On Gemini 2.5+, thinking is on by default and, left unset, the model manages its own
budget up to roughly 8,192 tokens — drawn from the same output allowance as the response text. A
`max_output_tokens` sized for the expected JSON alone gets consumed by reasoning and the answer is
truncated, surfacing as `finishReason: MAX_TOKENS` with **empty or partial text**. This is not a
hypothetical: it is among the most reported failure modes on the provider's own forum.

Reasoning content itself is never requested — `include_thoughts` stays off. It is not needed, and
storing it would put model-generated prose into a result whose every other field is either a document
value or an identifier.

**Consequence.** The truncation branch of R12 is load-bearing rather than defensive, and T059's
`max_output_tokens` measurement is what keeps it from firing in normal use.


## R15 — Prompt order is chosen for the cache, and the prefix is currently too short to be cached

**Decision.** The request is assembled stable-to-volatile — response shape, then the schema's
instructions and field descriptions, then the document text last.

**Rationale.** Gemini 2.5+ caches implicitly, with no configuration, and the provider's own guidance is
to "put large and common contents at the beginning of your prompt" and "send requests with similar
prefix in a short amount of time". That is prefix behaviour, and it confirms the ordering: everything
derived from `schema@version` is identical across every document extracted against that schema, and the
document is the only volatile part. Putting the document first — the natural reading order — would make
every extraction a cold read.

**Measured at T060, and confirmed.** A cache hit requires the shared prefix to exceed a per-model
minimum of **2,048–4,096 tokens**. The per-schema prefix measures **817 tokens** — the `invoice@1`
prompt is 489 tokens on its own, and only the prompt travels as the cached system instruction.
**It is below the threshold, so it is not cached at all today**, exactly as predicted.

So the ordering is correct and the benefit is currently zero. That is worth stating plainly rather than
leaving the earlier revision's implication that the ordering *buys* something today. Two honest options
exist and neither is decided here:

- Accept it. The prefix costs little to send, the ordering is already right, and a cache hit arrives free
  once schemas or instructions grow past the minimum.
- Pad the prefix deliberately to clear the threshold. Rejected as a default: paying for tokens whose only
  purpose is to make caching eligible is a cost decision dressed as an optimisation, and it should be
  measured before it is chosen.

**Consequence.** The ordering still needs its test, because the failure it guards is silent in the other
direction: once the prefix *is* large enough to cache, a volatile value interpolated ahead of the
breakpoint would leave results correct and multiply the bill. `tests/unit/test_prompt_assembly.py`
asserts it offline; T060 verifies a real cache read and, per the above, **is expected to report zero
until the prefix grows** — so it must assert the threshold arithmetic rather than assert a hit.

Cache hits are reported in the response's `usage.total_cached_tokens`. Nothing per-request — no
timestamp, no document id, no request id — may appear before the breakpoint.

## R16 — An adapter is selected by configuration, and the fixture adapter never is

**Decision.** `AdapterRegistry` holds the adapters an installation knows, `default_adapter()` returns
the first usable one in configured priority order, and application code calls that instead of
constructing a provider. The shape mirrors the ingest layer's parser registry so a reader who knows one
knows the other: an adapter whose extra is missing or whose credentials are absent is **recorded with
its reason** rather than omitted, priority decides, and the adapter id is a total tie-break so
selection never depends on registration order or dictionary iteration.

**Rationale.** FR-021 requires that a caller name no provider, model family, or model version anywhere
in application code. This entry exists because that requirement went **unmet for the whole of the
milestone's first implementation**: every documented example wrote `adapter=GeminiAdapter()`, which is
precisely what the requirement forbids, while `contracts/extraction-api.md` §8 asserted the opposite.
The gap was surfaced by a convergence pass reading the code against the spec, not by any of the tests —
because the tests had been written to match the code.

Worse, an earlier analysis pass had *touched* it: it found the quickstart claiming "no adapter argument:
configuration decides", judged that wrong about the design, and edited the documentation to match the
code rather than the code to match the spec. Treating an artifact as the thing to fix, when the artifact
was the only place still telling the truth, is the failure mode this entry is really about.

**The echo adapter is excluded from automatic selection, and that is a safety property.** It answers
from committed fixtures. If it were selectable when no real adapter is usable, a forgotten API key would
not produce an error — it would produce a stream of confident, fabricated extractions carrying full
provenance, indistinguishable downstream from real ones. That is the worst outcome this layer can have:
not a failure, but plausible wrong data with a content-addressed identity attesting to it.

So the exclusion is structural rather than a matter of priority ordering — `select()` skips it even when
it is ranked first and even when the alternative is raising. It stays fully usable when passed
explicitly, which is a decision a caller takes knowingly. `tests/unit/test_adapter_registry.py` pins
both halves.

**Alternatives.** Reading the adapter from an environment variable inside `extract()`: rejected because
it hides a result-affecting input inside a function that otherwise takes everything explicitly, and
provenance would record a choice the call site never made. Making `adapter` optional and defaulting to
`default_adapter()`: rejected for the MVP because a missing argument would then reach for a network
service, and an accidental extraction is worse than a `TypeError`.
