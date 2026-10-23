# Quality on your own documents

`docdoc eval` scores a committed golden set. This is the other half: a tenant measures docdoc on
*its own* documents, with no dataset files, and sees whether a change made things better or worse.
The decision is [ADR-0026](../adr/0026-tenant-quality-evaluation.md); the metrics are the ones
[evaluation](evaluation.md) defines, unchanged.

## Checking

Open a succeeded extraction run and mark a field **correct**, or **wrong** with the right value (or
"not present"). You choose which runs and fields; nothing is assigned and there is no list of
documents waiting. Over HTTP, a check is `POST /v1/runs/{run_id}/corrections`; in the console it is
the field table on the run detail, beside the page image. A check made in the console counts as the
tenant's own and records the operator as who checked.

A **correct** check keeps where the value was read from (page and box), so later runs can be
measured for reading it from the same place. A **wrong** check stores no location.

A **wrong** check needs a reason; the console will not save one without it. Its value is stored as
the type the schema declares for that field, as a dataset's labels are: `"2"` typed for a `number`
field is stored as the number 2, so it can match an extracted 2. A value the declared type cannot
carry is refused with `422 invalid_value`, naming only the field path.

## Answers

Each check is an answer. There is one answer per tenant, schema name, document and field path, and
the **newest check on any run of that document wins**. A changed mind is a new check; nothing is
edited or deleted. Answers **never expire**, and a run that carries a live answer is not swept by
retention. The old correction-retention setting is retired. `GET /v1/answers` summarises them
per schema name: documents and fields.

## Evaluations

`POST /v1/evaluations?schema=invoice` re-runs every document with an answer under the deployment's
**current** configuration (newest registered schema version, current parser and model routing),
scores the results against the answers, and stores a **report** recording that configuration. It
answers `202` with the document count, and the report is scored when every run has finished. A
document whose run fails counts as failed; it is not dropped. Answers and extracted values are typed
by the schema before scoring, as the dataset loaders type them.

Each evaluation has a `status`: `running`, `finished`, or `failed`. **Failed** means it could not be
scored at all, because its schema version was removed or edited after it started; `failure` names
the error class, and the next evaluation of that schema can start at once. A finished one carries a
`provenance` block saying which configuration produced it: model and parser ids and versions,
schema hashes and prompt hashes. The console's history shows the model and parser per re-run, and
marks a failed one "Could not be scored". A console start is logged with the operator's name. `GET /v1/evaluations` lists a
tenant's, and `GET /v1/evaluations/{id}` reads one with its per-field table and, per document, the
expected and extracted values.

Refusals:

| Status | When |
|---|---|
| `409 no_answers` | nothing has been checked for that schema |
| `409 evaluation_in_progress` | another for that tenant and schema is unfinished |
| `429` | the tenant's run limits are reached, with `Retry-After` |
| `404` | the schema is not configured, or the evaluation is not this tenant's |

## Schema versions

The library will not score or compare across a schema identity or hash change. So before each
evaluation the answers are **restamped** to the newest version: fields that no longer exist are
dropped from the stamped copy (not from storage), and new fields count as unchecked. The stamped
set has its own `golden_set_id`, so two reports over different answers or versions are visibly not
comparable, and the console marks a schema change in the history instead of diffing across it.

## Cost

An evaluation is N ordinary runs through the run queue. Unchanged stages are reused from the
content-addressed store, so an unchanged configuration costs nothing; a changed model or parser
calls the provider for each document. The console states N before it starts. Money is not estimated.

## Erasure

Erasing a document deletes its checks and removes it from every stored answer set and report: its
outcomes, values and locations go, the report's aggregates and `report_id` stay, and the evaluation
counts it in `erased_documents`. Erasing a tenant deletes its checks and evaluations. See
[retention](retention.md).

## Stored values

Tenant documents are scored at the restricted tier, so a stored report holds hashes. The values a
tenant sees are joined at read time from its own answers and each re-run's own artifacts.
