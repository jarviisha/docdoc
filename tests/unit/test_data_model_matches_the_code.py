"""T099 — the data model's field tables describe the models that exist.

`tests/unit/test_documented_api_references_resolve.py` checks the documents that
carry ```python blocks: it resolves imports and hand-listed attribute chains. It
cannot check `data-model.md`, which carries **no python blocks at all** — it
specifies the models as markdown tables, which is exactly the form that test's
regex does not see.

That gap let a real drift through. `data-model.md` listed `group_outcomes` and
`validation_verdicts` as fields of `EvaluationReport`; both lived one level down
on `report.metrics`, so a consumer following the data model wrote
`report.group_outcomes` and got `AttributeError`. Nothing failed, because nothing
was reading the tables.

So the tables are parsed and checked here. The rule is **one-directional**: every
field the data model names must exist on the model. A field the code has and the
document does not is *not* a failure — `EvaluationReport.dataset_size` arrived
with FR-009 after the table was written, and requiring the document to be
exhaustive would turn every additive change into a documentation edit before the
build goes green, which is how a check like this gets deleted.

**A property counts.** The storage is allowed to be normalized where duplicating
it would cost something real: outcomes live in one flat, totally ordered tuple and
`EvaluationReport.group_outcomes` delegates to `metrics`, because storing each
document's outcomes twice would double the bytes FR-043 requires to be identical.
What the data model describes is the surface a reader gets, not the layout.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

DATA_MODEL = pathlib.Path("specs/006-golden-set-evaluation/data-model.md")

#: `### EVA-9 · `DocumentPrediction`` -> ("EVA-9", "DocumentPrediction").
#:
#: The trailing `[^`]*` is load-bearing: EVA-30's heading is
#: ``EvaluationError(DocdocError)``, and an identifier-only pattern matched it
#: nowhere at all — so the entity vanished from the parse rather than failing
#: loudly. The mapping guard below is what surfaced that, which is the argument
#: for having it.
_HEADING = re.compile(r"^### (EVA-\d+[a-z]?) · `([A-Za-z_][A-Za-z0-9_]*)[^`]*`", re.M)

#: A table row's first cell, which may name more than one field:
#: `| `document_id`, `group_path` | `str` |`
_ROW = re.compile(r"^\|\s*(`[^|]+`)\s*\|", re.M)
_NAME = re.compile(r"`([a-z_][a-z0-9_]*)`")

#: Where each documented entity lives. Hand-maintained, and kept honest by
#: `test_every_documented_entity_is_mapped_or_skipped` below — a new EVA entry
#: with a field table fails until somebody says which model it is, or says why it
#: is not one.
MODELS = {
    "DocumentOrigin": "docdoc.evaluation:DocumentOrigin",
    "GoldenDocument": "docdoc.evaluation:GoldenDocument",
    "EntryKeySpec": "docdoc.evaluation:EntryKeySpec",
    "GoldenSet": "docdoc.evaluation:GoldenSet",
    "ExpectedLocation": "docdoc.evaluation:ExpectedLocation",
    "Label": "docdoc.evaluation:Label",
    "DocumentPrediction": "docdoc.evaluation:DocumentPrediction",
    "PredictionSet": "docdoc.evaluation:PredictionSet",
    "EntryAlignment": "docdoc.evaluation:EntryAlignment",
    "FieldOutcome": "docdoc.evaluation:FieldOutcome",
    "GroupOutcome": "docdoc.evaluation:GroupOutcome",
    "MetricValue": "docdoc.evaluation:MetricValue",
    "EvaluationOptions": "docdoc.evaluation:EvaluationOptions",
    "EvaluationReport": "docdoc.evaluation:EvaluationReport",
    "PartialDeclaration": "docdoc.evaluation:PartialDeclaration",
    "Correction": "docdoc.evaluation:Correction",
}

#: Documented entities that are deliberately not pydantic models, with the reason.
#: Naming them is what stops this map becoming a place to park a real drift.
NOT_MODELS = {
    "Tier": "a StrEnum; its members are checked by tests/unit/test_redaction.py",
    "golden_set_id": "a str, not a type — its formula is EVA-6 and identity.py",
    "FieldOutcomeKind": "a StrEnum; closure is asserted in test_location_agreement.py",
    "Comparator": "a concept, realised as the comparators.py registry",
    "LocationAgreement": "a StrEnum; its three values are asserted directly",
    "MetricDefinition": "realised as definitions.py's METRICS table of MetricSpec",
    "DocumentScore": "prose rather than a table — see the note in this module",
    "DatasetMetrics": "prose rather than a table",
    "EvaluationProvenance": "prose rather than a table; the 17 fields are "
    "enumerated in test_evaluation_refusals.py",
    "report_id": "a str, not a type — its formula is EVA-24 and identity.py",
    "Comparison": "prose rather than a table",
    "EvaluationError": "an exception; its attributes are asserted in the refusal tests",
}


def _documented() -> dict[str, tuple[str, tuple[str, ...]]]:
    """``{entity: (eva_id, fields)}`` for every heading that is followed by a table.

    A heading with no table before the next heading contributes no fields, which
    is how the prose entries (EVA-19, EVA-20, EVA-21) fall out naturally rather
    than needing to be special-cased.
    """
    text = DATA_MODEL.read_text(encoding="utf-8")
    headings = list(_HEADING.finditer(text))

    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.start() : end]

        fields: list[str] = []
        for row in _ROW.findall(section):
            if row.strip("`") in {"Field", "Value"}:
                continue
            fields.extend(_NAME.findall(row))
        found[heading.group(2)] = (heading.group(1), tuple(dict.fromkeys(fields)))
    return found


def _attributes(target: str) -> set[str]:
    """Every name a reader can reach on the model: fields, properties, methods."""
    module_name, type_name = target.split(":")
    obj = getattr(importlib.import_module(module_name), type_name)
    return set(getattr(obj, "model_fields", {})) | {
        name for name in dir(obj) if not name.startswith("_")
    }


def test_the_parser_finds_the_tables_it_is_meant_to() -> None:
    """A checker that parses nothing passes for the wrong reason."""
    documented = _documented()

    assert len(documented) >= 25, f"expected the data model's entities, found {len(documented)}"
    with_fields = {name for name, (_eva, fields) in documented.items() if fields}
    assert len(with_fields) >= 12, f"expected field tables, found {sorted(with_fields)}"
    assert documented["EvaluationReport"][1], "the report's table must be parsed"


@pytest.mark.parametrize("entity", sorted(MODELS))
def test_every_documented_field_exists_on_the_model(entity: str) -> None:
    """The assertion the first convergence pass had no way to make."""
    eva, fields = _documented()[entity]
    available = _attributes(MODELS[entity])

    missing = [field for field in fields if field not in available]
    assert not missing, (
        f"{eva} documents {entity} as carrying {missing}, and the model does not. "
        "A consumer following data-model.md reaches for those and gets an "
        "AttributeError. Either add them — a delegating property counts, where "
        "duplicating the storage would cost something — or the document is "
        "describing a model that no longer exists"
    )


def test_the_report_carries_the_navigation_the_data_model_promises() -> None:
    """EVA-23 spelled out, because it is the entry a reader starts from."""
    from docdoc.evaluation import EvaluationReport

    available = _attributes("docdoc.evaluation:EvaluationReport")
    for name in (
        "outcomes",
        "group_outcomes",
        "document_scores",
        "metrics",
        "validation_verdicts",
        "partial",
        "redacted_tiers",
        "provenance",
        "report_id",
    ):
        assert name in available, f"EvaluationReport has no {name!r}"

    # EVA-19's per-document view, which the flat storage supplies through these
    # rather than by keeping a second copy on each DocumentScore.
    assert hasattr(EvaluationReport, "outcomes_for")
    assert hasattr(EvaluationReport, "groups_for")


def test_every_documented_entity_is_mapped_or_skipped() -> None:
    """Keeps the two maps above honest as the data model grows.

    A new EVA entry fails here until somebody either points it at a model or
    states why it is not one. Without this, the check silently stops covering
    whatever was added last — which is the failure mode of every hand-maintained
    list in this repository, and the reason each of them has a guard like this.
    """
    documented = set(_documented())
    accounted = set(MODELS) | set(NOT_MODELS)

    unaccounted = sorted(documented - accounted)
    assert not unaccounted, (
        f"the data model documents {unaccounted}, which this file neither checks "
        "nor explains. Add it to MODELS, or to NOT_MODELS with the reason"
    )

    stale = sorted(accounted - documented)
    assert not stale, (
        f"{stale} are listed here and appear in no data-model heading; they were "
        "renamed or removed, and this map is now describing nothing"
    )


def test_the_check_can_actually_fail() -> None:
    """Guards the guard.

    The parser is a regex over markdown, which is the kind of thing that quietly
    matches everything or nothing. This confirms it reads real field names and
    that an absent one would be caught.
    """
    fields = _documented()["DocumentPrediction"][1]

    assert "failed_stage" in fields
    assert "failure_reason" in fields
    assert "Field" not in fields, "the table header leaked into the field list"

    available = _attributes("docdoc.evaluation:DocumentPrediction")
    assert "failed_stage" in available
    assert "a_field_that_was_never_written" not in available


# -- Milestone 9's data model, which describes a table (T112) ------------------
#
# Everything above reads `specs/006-golden-set-evaluation/data-model.md` and
# nothing else. This repository now has three data models and one of them was
# checked, which is the shape of gap this whole file was written about: the
# `EvaluationReport` drift survived because "nothing was reading the tables", and
# an eighteen-row table for `Run` was in exactly that position.
#
# **It describes database columns, not model fields**, and the difference is not
# cosmetic. `cancel_requested` is a column and deliberately *not* a `Run` field —
# `PostgresRunQueue._row_to_run` pops it as transport state — so checking this
# table against the pydantic model would fail on a row that is correct. The
# migration is what the table describes, so the migration is what it is checked
# against.
#
# The one-directional rule above is kept for the same reason it exists there: a
# column the SQL has and the document does not is not a failure, or every
# additive migration needs a documentation edit before the build goes green.

RUNS_DATA_MODEL = pathlib.Path("specs/009-asynchronous-runs/data-model.md")
RUNS_MIGRATION = pathlib.Path("src/docdoc/runs/migrations/0001_runs.sql")

#: A column definition in the `CREATE TABLE runs` body: four leading spaces, a
#: lowercase name, then a Postgres type. Anchored on the type so that a
#: `CONSTRAINT` line and a continuation of a `CHECK` expression are not read as
#: columns.
_SQL_COLUMN = re.compile(
    r"^    ([a-z_]+)\s+(?:uuid|text|integer|timestamptz|jsonb|boolean)\b", re.M
)


def _documented_run_columns() -> tuple[str, ...]:
    """Every column the `## The Run` table names, in order.

    Reuses `_ROW` and `_NAME` above rather than a second pair of patterns — the
    table shape is the same, and two copies would be two things to keep in step.
    """
    text = RUNS_DATA_MODEL.read_text(encoding="utf-8")
    section = text[text.index("## The Run") : text.index("## States")]

    found: list[str] = []
    for row in _ROW.findall(section):
        if row.strip("`") == "Field":
            continue
        found.extend(_NAME.findall(row))
    return tuple(dict.fromkeys(found))


def _migration_columns() -> set[str]:
    return set(_SQL_COLUMN.findall(RUNS_MIGRATION.read_text(encoding="utf-8")))


def test_the_run_table_is_parsed_at_all() -> None:
    """A parser that finds nothing passes for the wrong reason.

    Eighteen is the count at the time of writing and is asserted as a floor
    rather than an equality: a column added later should not fail this, it should
    fail the check below only if the document and the SQL disagree.
    """
    documented = _documented_run_columns()

    assert len(documented) >= 18, (
        f"the Run table parsed to {len(documented)} columns; the section was "
        f"restructured or the row matcher no longer matches, and this check is "
        f"now checking nothing"
    )
    assert "run_id" in documented
    assert "cancel_requested" in documented, (
        "the column that is a column and not a model field is missing from the "
        "parse, so the distinction this check exists to handle is untested"
    )


def test_the_migration_is_parsed_at_all() -> None:
    """Guards the other half: an empty column set would make every claim pass."""
    columns = _migration_columns()

    assert len(columns) >= 18, f"only {len(columns)} columns parsed from the migration"
    assert {"run_id", "tenant_id", "status", "cancel_requested"} <= columns


def test_every_documented_run_column_exists_in_the_migration() -> None:
    """The assertion. A reader following the data model must find the column.

    One-directional, exactly as the evaluation half above is: this fails when the
    document names a column the table does not have, and stays silent when the
    table has one the document does not.
    """
    absent = sorted(set(_documented_run_columns()) - _migration_columns())

    assert not absent, (
        f"data-model.md names these columns and `0001_runs.sql` creates none of "
        f"them: {absent}. A reader following the data model writes a query that "
        f"fails, which is the drift this file exists to catch"
    )


def test_the_run_table_describes_columns_and_not_model_fields() -> None:
    """The distinction that decides what this is checked against.

    Pinned rather than left in a comment: if `cancel_requested` ever becomes a
    `Run` field, the reason for checking against SQL rather than against the
    model has gone, and somebody should re-read the choice rather than inherit
    it.
    """
    from docdoc.runs.model import Run

    assert "cancel_requested" not in Run.model_fields, (
        "`cancel_requested` is now a Run field. This check compares the data "
        "model against the migration precisely because it was not — re-examine "
        "whether the pydantic model is the better target now"
    )
    assert "cancel_requested" in _migration_columns()


# -- and its Indexes table, which is where the drift actually was --------------
#
# The columns matched. The *index* did not: the table specified
# `(status, created_at)` partial and the migration creates `(created_at)` with
# status in the predicate. Nothing compared them, so the document described an
# index the database has never had.
#
# Checked on the **key columns**, because that is what was wrong and what a
# reader planning capacity or reading a query plan would go looking for. The
# predicate is prose in one and SQL in the other, and matching it textually would
# be a check that fails on a reformatting.

_DOC_INDEX = re.compile(r"^\|\s*`\(([^)]*)\)`", re.M)
_SQL_INDEX = re.compile(r"^\s*ON runs \(([^)]*)\)", re.M)


def _documented_indexes() -> set[tuple[str, ...]]:
    text = RUNS_DATA_MODEL.read_text(encoding="utf-8")
    section = text[text.index("## Indexes") :]
    return {tuple(part.strip() for part in row.split(",")) for row in _DOC_INDEX.findall(section)}


def _migration_indexes() -> set[tuple[str, ...]]:
    """Every index the migration creates, including the primary key.

    The primary key is added by hand because it is declared inline on the column
    rather than as a `CREATE INDEX`, and the data model lists it as an index —
    correctly, since it is the one the API's point lookup uses.
    """
    sql = RUNS_MIGRATION.read_text(encoding="utf-8")
    found = {tuple(part.strip() for part in match.split(",")) for match in _SQL_INDEX.findall(sql)}
    primary = re.search(r"^    ([a-z_]+)\s+\S+\s+PRIMARY KEY", sql, re.M)
    assert primary, "the runs table declares no primary key"
    found.add((primary.group(1),))
    return found


def test_the_index_tables_are_parsed_at_all() -> None:
    """Guards both sides: an empty set on either makes the comparison vacuous."""
    assert len(_documented_indexes()) == 4, (
        f"the Indexes table parsed to {len(_documented_indexes())} rows, not four"
    )
    assert len(_migration_indexes()) == 4, (
        f"the migration parsed to {len(_migration_indexes())} indexes, not four"
    )


def test_the_documented_indexes_are_the_indexes_the_migration_creates() -> None:
    """The check that was missing, on the key columns.

    `(status, created_at)` versus `(created_at)` is not a cosmetic difference: a
    composite index led by `status` cannot produce globally ordered `created_at`
    across the two values the partial predicate admits, so the claim query's
    `ORDER BY created_at` would need a sort. The database has the right index and
    the document described a different one for four convergence passes.
    """
    documented = _documented_indexes()
    created = _migration_indexes()

    assert documented == created, (
        f"data-model.md and 0001_runs.sql disagree about the indexes.\n"
        f"  documented and absent: {sorted(documented - created)}\n"
        f"  created and undocumented: {sorted(created - documented)}"
    )


# -- every table, not just the interesting one (T115) --------------------------
#
# The milestone creates three tables and the data model described one. The
# `runs` check above would never have found that: it starts from the document and
# asks whether the code has what the document names, which says nothing about a
# table the document forgot.
#
# So this asks the other question. It is the same shape as the sweep in
# `test_documented_api_references_resolve.py` — start from the code, not the list
# — and it exists for the same reason: a list-driven check cannot see what is
# missing from the list.

_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+\{?([a-z_]+)\}?", re.I)


def _created_tables() -> set[str]:
    """Every table the migrations create, including the runner's own.

    `docdoc_schema_version` is created from an f-string in
    `migrations/__init__.py` rather than from a `.sql` file, so the `{` and `}`
    in the pattern are load-bearing: a matcher over the SQL files alone would
    miss the table the runner itself needs and report two of three.
    """
    sources = [*RUNS_MIGRATION.parent.glob("*.sql"), RUNS_MIGRATION.parent / "__init__.py"]
    found: set[str] = set()
    for path in sources:
        for name in _CREATE_TABLE.findall(path.read_text(encoding="utf-8")):
            found.add("docdoc_schema_version" if name == "APPLIED_TABLE" else name)
    return found


def test_the_table_scan_finds_all_three() -> None:
    """Guards the guard: a scan that found one would make the check below vacuous."""
    tables = _created_tables()

    assert tables == {"runs", "docdoc_default_tenant", "docdoc_schema_version"}, (
        f"the migrations create {sorted(tables)}; the scan or the migrations "
        f"changed, and the check below is now describing something else"
    )


#: The heading that documents each table. A map rather than a search for the
#: table's name, because the run's section is headed "The Run" — the document
#: describes the *entity* and the table is how it is stored, which is the right
#: way round for a data model and the wrong way round for a substring match.
#:
#: Kept honest in both directions by the two checks below: a table with no entry
#: fails, and an entry naming a heading the document does not have fails too.
TABLE_SECTIONS = {
    "runs": "## The Run",
    "docdoc_default_tenant": "### `docdoc_default_tenant`",
    "docdoc_schema_version": "### `docdoc_schema_version`",
}


def test_every_table_the_migrations_create_has_a_section() -> None:
    """The direction the `runs` check cannot see.

    That check starts from the document and asks whether the code has what the
    document names, which says nothing about a table the document forgot — and
    it forgot two of three. `docdoc_default_tenant` was the one that mattered: it
    carries FR-089's assignment and a refusal to change that strands content if
    it is overridden.
    """
    unmapped = sorted(table for table in _created_tables() if table not in TABLE_SECTIONS)

    assert not unmapped, (
        f"these tables are created and this map does not say where they are "
        f"documented: {unmapped}. A reader comparing the document against the "
        f"database finds something the document does not mention, which is how a "
        f"document stops being trusted"
    )


def test_every_mapped_section_exists_in_the_document() -> None:
    """The other half: an entry pointing at a heading that was renamed away."""
    text = RUNS_DATA_MODEL.read_text(encoding="utf-8")

    absent = sorted(
        f"{table} -> {heading}" for table, heading in TABLE_SECTIONS.items() if heading not in text
    )

    assert not absent, (
        f"these sections are claimed here and are not in data-model.md: {absent}. "
        f"The heading was renamed and this map now describes nothing"
    )


# -- T120: the contract's protocol block describes the protocol ----------------
#
# The same drift as everything above, in the file that publishes an interface for
# other people to implement. `contracts/runs-layer.md` opens with a ```python
# block giving `RunQueue`'s nine signatures, and five of them had fallen behind
# the code: `finish` was shown returning `None` and without `worker_id` or
# `only_from`, `heartbeat` and `release` without `worker_id`, `claim` without
# `max_attempts`, `submit` without `expires_at`.
#
# Three of those absences were not cosmetic. `worker_id` on the three lease
# operations *is* the fix for the ownership defects a review found — so the
# contract was publishing precisely the surface that let a superseded worker
# overwrite the live attempt's verdict, requeue a run another worker was
# executing, and extend a lease it no longer held. Anyone implementing this
# protocol from the document would have rebuilt all three.
#
# `test_documented_api_references_resolve.py` cannot catch it: that one checks
# that names *resolve*, and every name here resolves. A signature can be wrong in
# every parameter while naming only things that exist.


#: Relative, like every other path in this file, so the check runs from the repo
#: root the way the suite does.
RUNS_LAYER_CONTRACT = pathlib.Path("specs/009-asynchronous-runs/contracts/runs-layer.md")


def _documented_queue_signatures() -> dict[str, str]:
    """The `def` lines from the contract's protocol block, one per method.

    Signatures are wrapped across lines to stay inside the file's column width,
    so they are flattened before comparison — the contract is about the surface,
    not about where the author put the newlines.
    """
    import re

    text = RUNS_LAYER_CONTRACT.read_text(encoding="utf-8")
    block = text.split("```python", 1)[1].split("```", 1)[0]
    flat = re.sub(r",\s*\n\s+", ", ", block)

    found = {}
    for line in flat.splitlines():
        stripped = line.strip()
        match = re.match(r"def (\w+)\(", stripped)
        if match:
            # Drop the `: ...` body; the signature is the whole of the claim.
            signature = re.sub(r"\s+", " ", stripped).removesuffix(" ...").rstrip(":").strip()
            found[match.group(1)] = signature
    return found


def test_the_protocol_block_is_parsed_at_all() -> None:
    """The guard every parser in this file has, for the reason they all give.

    A regex that silently matches nothing turns this check into a test that
    always passes, which is worse than not having it.
    """
    documented = _documented_queue_signatures()

    assert len(documented) >= 9, (
        f"only {len(documented)} signatures were parsed out of the protocol block; "
        "the block's shape changed and the check below is now vacuous"
    )


@pytest.mark.parametrize(
    "method",
    [
        "submit",
        "get",
        "claim",
        "heartbeat",
        "release",
        "finish",
        "cancel",
        "is_cancelled",
        "ping",
    ],
)
def test_every_documented_queue_signature_matches_the_protocol(method: str) -> None:
    """Exact match, both directions, which is the opposite of the rule above.

    The field tables are checked one-directionally — a field the code has and the
    document does not is not a failure — because requiring exhaustiveness there
    turns every additive change into a documentation edit. A *signature* is
    different: an argument the code takes and the document omits is not extra
    detail, it is a caller who cannot call the method, and an argument the
    document shows and the code lacks is a caller who gets a `TypeError`. There
    is no additive change to a signature that leaves the old one true.
    """
    import inspect
    import re

    from docdoc.runs.queue import RunQueue

    documented = _documented_queue_signatures()
    assert method in documented, (
        f"`{method}` is on the protocol and not in the contract's block. A method "
        "nobody documented is one an implementer will not implement"
    )

    # `from __future__ import annotations` makes every annotation a string, so
    # `inspect.signature` renders them quoted. The quotes are an artefact of how
    # the module defers evaluation, not part of the surface being published.
    rendered = str(inspect.signature(getattr(RunQueue, method))).replace("'", "")
    real = re.sub(r"\s+", " ", f"def {method}{rendered}")

    assert documented[method] == real, (
        f"the contract publishes a `{method}` the protocol does not have.\n"
        f"  contract: {documented[method]}\n"
        f"  code:     {real}\n"
        "This block is what somebody implementing RunQueue reads, so a stale "
        "signature here is a defect they will faithfully reproduce."
    )
