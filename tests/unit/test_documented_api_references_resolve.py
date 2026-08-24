"""T109 — the API the documentation promises is the API that exists (SC-020).

T102 made `examples/` executable on one argument: prose examples are the only code
nobody runs, so they are wrong the next time a constructor changes, and the person
who finds out is the new contributor they were written for. That argument does not
stop at `examples/`. `README.md`, `docs/concepts/extraction.md`, `quickstart.md`,
and the API contract carry python blocks that nothing executes either.

A convergence pass checked every one of them by hand — each import, each method,
`identities()` returning exactly the documented tuple, the `sha256:` hash prefix in
the README — and found them all correct. That is precisely why this file exists.
There was nothing to repair; there was only a verification that had happened once
and could not happen again. The next signature change breaks the docs silently, and
the pass after that finds it by hand a second time.

**This checks references, not execution, and the difference is deliberate.** The
blocks use undefined names on purpose — `document`, `huge_document`,
`span_over_pages_1_to_5` — because a documentation example should show the shape of
a call, not carry the setup needed to run it. Executing them would mean rewriting
them into something less readable to satisfy a test, which trades the thing that
makes them useful for the thing that makes them checkable. So this resolves what
can be resolved statically: every `from docdoc… import X` names a real export, and
every attribute reached on a docdoc type is a real attribute.

What that leaves uncovered is stated rather than implied: argument names, argument
types, and return values are not checked. A documented call that passes the wrong
keyword still passes this file. Full execution and doctests both remain available
if that gap ever bites; it has not yet, and building the heavier mechanism against
a hypothetical is how test suites get slow without getting safer.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

#: Every document that shows a reader how to call this layer. `contracts/` is here
#: because it is the normative description of the API, so a drift between it and
#: the code is a contract that no longer describes anything.
DOCUMENTS = (
    "README.md",
    "docs/concepts/extraction.md",
    "docs/concepts/validation.md",
    "specs/003-schema-driven-extraction/quickstart.md",
    "specs/003-schema-driven-extraction/contracts/extraction-api.md",
    "specs/005-deterministic-validation/contracts/validation-api.md",
    "docs/concepts/evaluation.md",
    "specs/006-golden-set-evaluation/contracts/evaluation-api.md",
)

_BLOCK = re.compile(r"```python\n(.*?)```", re.S)
_IMPORT_FROM = re.compile(r"^from\s+(docdoc[\w.]*)\s+import\s+([^\n#]+)", re.M)


def _python_blocks(document: str) -> list[str]:
    return _BLOCK.findall(pathlib.Path(document).read_text(encoding="utf-8"))


def _imported_names(block: str) -> list[tuple[str, str]]:
    """(module, name) for every `from docdoc… import …` in a block.

    Comments are stripped before splitting on commas. The first version of this
    helper did not, so `import default_adapter  # the protocol, and selection`
    parsed as three imported names, two of which were English. A checker that
    reports invented failures gets ignored as fast as one that reports none.
    """
    pairs: list[tuple[str, str]] = []
    for module, names in _IMPORT_FROM.findall(block):
        for raw in names.split(","):
            name = raw.split("#")[0].strip().split(" as ")[0].strip().strip("()")
            if name:
                pairs.append((module, name))
    return pairs


@pytest.mark.parametrize("document", DOCUMENTS)
def test_the_document_has_python_blocks_to_check(document: str) -> None:
    """A checker that finds no blocks passes for the wrong reason."""
    assert _python_blocks(document), (
        f"{document} has no ```python blocks; either it was restructured or the fence "
        "matcher no longer matches, and this file is now checking nothing"
    )


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_documented_import_resolves(document: str) -> None:
    """The assertion. A documented import that fails is a broken first impression."""
    broken: list[str] = []
    for block in _python_blocks(document):
        for module, name in _imported_names(block):
            try:
                target = importlib.import_module(module)
            except ImportError as exc:
                broken.append(f"`import {module}` -> {type(exc).__name__}: {exc}")
                continue
            if not hasattr(target, name):
                broken.append(f"`from {module} import {name}` -> not exported")
    assert not broken, f"{document} documents imports that do not resolve: {broken}"


def test_the_import_check_can_actually_fail() -> None:
    """Guards the guard, on both directions of the comment-stripping bug."""
    block = (
        "from docdoc.extraction import extract   # a real one, and a comment\n"
        "from docdoc.extraction import no_such_symbol\n"
    )
    pairs = _imported_names(block)
    assert ("docdoc.extraction", "extract") in pairs
    assert ("docdoc.extraction", "no_such_symbol") in pairs
    assert not any(" " in name for _, name in pairs), (
        "a comment leaked into an imported name; the checker would report English "
        "prose as a missing export"
    )

    module = importlib.import_module("docdoc.extraction")
    assert hasattr(module, "extract"), "a known export must be found"
    assert not hasattr(module, "no_such_symbol"), (
        "an absent export must not be found, or the check passes for anything"
    )


#: Attribute chains the documents promise on docdoc objects, each paired with the
#: type that must carry it. Hand-listed because the objects are built at runtime in
#: the prose (`result`, `registry`) and cannot be resolved statically from the text.
#: Every entry here was found in a document, not invented.
DOCUMENTED_ATTRIBUTES = (
    ("docdoc.extraction:ExtractionResult", ("values", "provenance", "artifact_id", "value_at")),
    ("docdoc.extraction:SchemaRegistry", ("from_paths", "describe", "identities", "resolve")),
    ("docdoc.extraction.adapters:EchoAdapter", ("from_fixtures", "malformed")),
    (
        "docdoc.extraction.adapter:ModelUsage",
        ("input_tokens", "output_tokens", "cache_read_input_tokens"),
    ),
    (
        "docdoc.extraction.extract:ExtractionProvenance",
        (
            "document_id",
            "schema_identity",
            "schema_hash",
            "adapter_id",
            "model_id",
            "model_version",
            "usage",
        ),
    ),
    (
        "docdoc.evaluation:EvaluationReport",
        (
            "outcomes",
            "metrics",
            "document_scores",
            "partial",
            "redacted_tiers",
            "provenance",
            "report_id",
        ),
    ),
    (
        "docdoc.evaluation:DatasetMetrics",
        ("micro", "macro", "per_field_path", "group_outcomes", "validation_verdicts"),
    ),
    ("docdoc.evaluation:MetricValue", ("value", "numerator", "denominator", "averaging")),
    (
        "docdoc.evaluation:PartialDeclaration",
        ("skipped_documents", "skipped_tiers", "covered_labels", "declared_labels"),
    ),
    (
        "docdoc.evaluation:Comparison",
        ("metrics", "grounding_regression", "provenance_differences", "changed_outcomes"),
    ),
    (
        "docdoc.evaluation:Correction",
        (
            "field_path",
            "predicted_value",
            "corrected_value",
            "location",
            "reason",
            "annotator",
            "timestamp",
        ),
    ),
    (
        "docdoc.evaluation:FieldOutcome",
        ("expected_hash", "predicted_hash", "comparator_version", "grounding_status"),
    ),
)


@pytest.mark.parametrize(("target", "attributes"), DOCUMENTED_ATTRIBUTES)
def test_documented_attributes_exist(target: str, attributes: tuple[str, ...]) -> None:
    """Catches the rename that leaves the import working and the example wrong."""
    module_name, type_name = target.split(":")
    obj = getattr(importlib.import_module(module_name), type_name)
    fields = set(getattr(obj, "model_fields", {}))
    missing = [a for a in attributes if not hasattr(obj, a) and a not in fields]
    assert not missing, (
        f"{target} no longer has {missing}, which the documentation still shows being used"
    )


@pytest.mark.parametrize("name", [a for _, attrs in DOCUMENTED_ATTRIBUTES for a in attrs])
def test_each_documented_attribute_appears_in_a_document(name: str) -> None:
    """Keeps the list above honest in the other direction.

    A hand-maintained list of things to check rots the same way the plan's test tree
    did. This asserts every entry is genuinely documented somewhere, so a name that
    stops appearing in the docs stops being claimed here.
    """
    corpus = "".join(pathlib.Path(d).read_text(encoding="utf-8") for d in DOCUMENTS)
    assert name in corpus, (
        f"`{name}` is checked here but appears in none of the documents, so this entry "
        "asserts something no reader was ever promised"
    )


# -- configuration names (T124) ----------------------------------------------
#
# The same argument as the imports above, applied to the other thing the docs
# promise: environment variable names. They are string literals, so nothing here
# resolved them — a renamed constant would leave seven documents confidently
# wrong and every test green.
#
# This is the last hand-maintained duplication this milestone introduced. T120
# closed a documentation gap by writing the same table into three artifacts, and
# T105/T106 already established what happens to a hand-maintained list here: it
# goes stale exactly when nobody is looking at it. So it is checked rather than
# trusted, in both directions.

#: Every module that defines a `DOCDOC_*` configuration name, and the constants it
#: defines them as. Both docdoc layers, not just extraction: the convention is
#: repo-wide and a check that covered one layer would go stale at the boundary.
CONFIG_MODULES = {
    "docdoc.extraction.registry": ("SCHEMA_PATHS_ENV",),
    "docdoc.extraction.adapter_registry": ("ADAPTERS_ENV", "ECHO_FIXTURES_ENV"),
    "docdoc.extraction.adapters.gemini": ("MODEL_ENV",),
    "docdoc.ingest.parsers.azure_di": ("ENDPOINT_ENV", "KEY_ENV"),
    "docdoc.cli.config": ("STORE_ROOT_ENV",),
    "docdoc.grounding.view": ("MATCH_VIEW_CACHE_ENV",),
    # `settings`, not `app`: `app` imports FastAPI, and this check runs on a
    # base install that has no extras.
    "docdoc.api.settings": ("REQUEST_BYTES_ENV",),
    "docdoc.ingest.source": ("MAX_DOCUMENT_BYTES_ENV", "MAX_PAGES_ENV"),
}

#: Wider than DOCUMENTS: configuration is described in places that carry no python
#: block at all, and those are exactly the ones an import check cannot reach.
CONFIG_DOCUMENTS = (
    *DOCUMENTS,
    "CONTRIBUTING.md",
    "specs/003-schema-driven-extraction/data-model.md",
    "specs/003-schema-driven-extraction/research.md",
)

#: Requires at least one character after the prefix, so the `DOCDOC_*` wildcard
#: idiom the prose uses is not mistaken for a variable named `DOCDOC_`.
_ENV_NAME = re.compile(r"\bDOCDOC_[A-Z0-9_]+\b")


def _defined_env_names() -> dict[str, str]:
    """{value: "module.CONSTANT"} for every configuration name the code defines."""
    defined: dict[str, str] = {}
    for module_name, constants in CONFIG_MODULES.items():
        module = importlib.import_module(module_name)
        for constant in constants:
            assert hasattr(module, constant), (
                f"{module_name}.{constant} is listed here but does not exist; it was "
                "renamed or removed, and this map is now describing nothing"
            )
            defined[getattr(module, constant)] = f"{module_name}.{constant}"
    return defined


@pytest.mark.parametrize("document", CONFIG_DOCUMENTS)
def test_every_documented_configuration_name_exists(document: str) -> None:
    """A documented variable the code never reads is worse than an undocumented one.

    The reader exports it, nothing happens, and there is no error to search for --
    the failure is silence.
    """
    defined = _defined_env_names()
    text = pathlib.Path(document).read_text(encoding="utf-8")
    unknown = sorted({name for name in _ENV_NAME.findall(text) if name not in defined})
    assert not unknown, (
        f"{document} documents configuration names that no module defines: {unknown}. "
        f"Defined: {sorted(defined)}"
    )


def test_every_configuration_name_is_documented() -> None:
    """The other direction. A variable readers cannot discover configures nothing.

    Extraction's three are documented in the contract and the concept doc; ingest's
    two came with Milestone 2 and are named in this feature's research note as the
    precedent the extraction names follow.
    """
    documented: set[str] = set()
    for document in CONFIG_DOCUMENTS:
        documented |= set(_ENV_NAME.findall(pathlib.Path(document).read_text(encoding="utf-8")))

    undocumented = sorted(
        f"{name} ({where})"
        for name, where in _defined_env_names().items()
        if name not in documented
    )
    assert not undocumented, (
        f"these configuration names are read by the code and documented nowhere a reader looks: "
        f"{undocumented}"
    )


def test_the_configuration_check_can_actually_fail() -> None:
    """Guards the guard. The matcher is a regex over prose, which is the kind of
    thing that quietly matches everything or nothing."""
    assert _ENV_NAME.findall("set DOCDOC_SCHEMA_PATHS and DOCDOC_GEMINI_MODEL") == [
        "DOCDOC_SCHEMA_PATHS",
        "DOCDOC_GEMINI_MODEL",
    ]
    assert _ENV_NAME.findall("clears `DOCDOC_*` for every test") == [], (
        "the wildcard idiom must not be read as a variable name"
    )
    assert "DOCDOC_SCHEMA_PATHS" in _defined_env_names(), "a known-real name must be found"
    assert "DOCDOC_NOT_A_REAL_NAME" not in _defined_env_names()
