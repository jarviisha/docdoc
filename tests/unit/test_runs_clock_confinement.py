"""`identity.py` is the only module in `docdoc.runs` that may *read* a clock.

The kernel's determinism guard is an AST scan and a runtime audit hook, and it
covers the kernel. Milestone 9 is the first work above it that genuinely needs a
clock and a random source, so the risk this file addresses is **drift**: a lease
comparison written inline in `postgres.py` passes every existing check, and the
next one lands in `worker.py`, and by the time anything notices the claim policy
is no longer testable at an arbitrary instant.

That is not a style concern. ADR-0013 §4 makes at-least-once delivery safe by
observing that re-executing a stage cannot produce a different answer, and that
holds only while the layers below stay pure. A clock that crept downward would
make a redelivered run able to disagree with the one it replaced.

**What is forbidden is reaching past `identity.py` to the standard library.**
Calling `docdoc.runs.identity.now()` is the sanctioned way to obtain an instant —
somebody has to, and that module exists to be the somebody. So this resolves each
call back to where its name came from, rather than matching the name alone.

An earlier version of this test matched bare call names, and `worker.py` passed it
by importing `now as clock`. The alias was written to avoid a false positive and
it also disabled the check, which is the precise failure mode a guard that cannot
see provenance will always have.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RUNS = Path(__file__).resolve().parents[2] / "src" / "docdoc" / "runs"

#: Standard-library modules that produce values from ambient state.
FORBIDDEN_MODULES = frozenset({"datetime", "time", "uuid", "random", "secrets"})

#: The one module allowed to import them, and the reason it exists.
PERMITTED = "identity.py"

#: Calls that read ambient state, by attribute name. Used for the
#: ``module.attr()`` form, where the module is named at the call site.
_READERS = frozenset(
    {
        "now",
        "utcnow",
        "today",
        "fromtimestamp",
        "time",
        "time_ns",
        "monotonic",
        "monotonic_ns",
        "perf_counter",
        "uuid1",
        "uuid3",
        "uuid4",
        "uuid5",
        "randint",
        "randrange",
        "choice",
        "shuffle",
        "getrandbits",
        "token_bytes",
        "token_hex",
        "token_urlsafe",
    }
)


def _modules() -> list[Path]:
    found = sorted(p for p in RUNS.rglob("*.py") if p.name != PERMITTED)
    assert found, "no modules found; this test would pass vacuously"
    return found


def _forbidden_reads(source: str) -> set[str]:
    """Calls that reach a forbidden stdlib module, however the name was bound.

    Two forms, and both are resolved through the import that introduced the name
    rather than by matching the name itself:

        import datetime        -> datetime.now()      caught via the module name
        from time import monotonic; monotonic()       caught via the binding
        from docdoc.runs.identity import now; now()   permitted, it is not stdlib
    """
    tree = ast.parse(source)

    # name -> the top-level module it was imported from
    origin: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                origin[alias.asname or root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            root = node.module.split(".")[0]
            for alias in node.names:
                origin[alias.asname or alias.name] = root

    offending: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            # `datetime.now()`, and also `datetime.datetime.now()` — walk down to
            # the root name, because `import datetime` then the doubled form is
            # the most common way to read a clock in Python and matching only the
            # single-attribute shape would miss it.
            base: ast.expr = func.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if (
                isinstance(base, ast.Name)
                and origin.get(base.id) in FORBIDDEN_MODULES
                and func.attr in _READERS
            ):
                offending.add(f"{base.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            # `monotonic()` — permitted unless the name came from a stdlib clock.
            if origin.get(func.id) in FORBIDDEN_MODULES:
                offending.add(func.id)
    return offending


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_only_identity_may_read_a_clock_or_a_random_source(module: Path) -> None:
    offending = sorted(_forbidden_reads(module.read_text(encoding="utf-8")))
    assert not offending, (
        f"{module.name} reads {offending} from the standard library. Only "
        f"`{PERMITTED}` may: every other module takes `now` and `run_id` as "
        f"parameters, or calls `docdoc.runs.identity` (FR-072). That is what "
        f"keeps the claim policy testable at an arbitrary instant and keeps the "
        f"layers below this one deterministic"
    )


def test_identity_is_where_the_clock_actually_lives() -> None:
    """Guards the guard: the exemption must be exempting something."""
    source = (RUNS / PERMITTED).read_text(encoding="utf-8")
    assert _forbidden_reads(source), (
        f"{PERMITTED} reads no clock and no random source, so the exemption above "
        "is protecting nothing and the check has gone vacuous"
    )


def test_an_alias_does_not_defeat_the_check() -> None:
    """The regression that motivated rewriting this file.

    `from datetime import datetime as dt` then `dt.now()` matched nothing when
    the check compared call names, and that is exactly what a developer writes
    to silence a false positive.
    """
    source = "from datetime import datetime as dt\ndef f():\n    return dt.now()\n"
    assert _forbidden_reads(source) == {"dt.now"}

    source = "import time as t\nx = t.monotonic()\n"
    assert _forbidden_reads(source) == {"t.monotonic"}


def test_calling_identitys_clock_is_permitted() -> None:
    """`identity.now()` is the sanctioned way to obtain an instant."""
    source = "from docdoc.runs.identity import now\ndef f():\n    return now()\n"
    assert not _forbidden_reads(source)


def test_a_type_annotation_is_not_a_read() -> None:
    source = "from datetime import datetime\nclass M:\n    at: datetime\n"
    assert not _forbidden_reads(source)


def test_a_clock_read_inside_a_function_is_found() -> None:
    """Not at module level, where anyone would see it."""
    source = "from time import monotonic\ndef f():\n    return monotonic()\n"
    assert _forbidden_reads(source) == {"monotonic"}


def test_the_doubled_module_form_is_found() -> None:
    """`import datetime` then `datetime.datetime.now()` — the commonest shape."""
    source = "import datetime\ndef f():\n    return datetime.datetime.now()\n"
    assert _forbidden_reads(source) == {"datetime.now"}
