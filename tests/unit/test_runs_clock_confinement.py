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

**This checks calls, not imports, and the distinction is the whole design.**
`model.py` imports `datetime` and `UUID` because its fields are typed with them,
and `queue.py` imports both under `TYPE_CHECKING` for annotations. Neither reads
anything. Banning the import would ban expressing "this field holds an instant",
which is not what FR-072 asks for and would push those types out of the models
where they belong. What the rule actually forbids is *producing* a value from
ambient state: `datetime.now()`, `uuid4()`, `time.monotonic()`. So that is what
is detected.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RUNS = Path(__file__).resolve().parents[2] / "src" / "docdoc" / "runs"

#: Callables that read ambient state. Matched on the name at the call site, so
#: both `datetime.now(...)` and a bare `now(...)` imported from `datetime` are
#: caught -- the second being the shape a well-meant "tidy the imports" produces.
FORBIDDEN_CALLS = frozenset(
    {
        # datetime
        "now",
        "utcnow",
        "today",
        "fromtimestamp",
        # time
        "time",
        "time_ns",
        "monotonic",
        "monotonic_ns",
        "perf_counter",
        # uuid
        "uuid1",
        "uuid3",
        "uuid4",
        "uuid5",
        # random / secrets
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

#: The one module allowed to, and the reason it exists.
PERMITTED = "identity.py"


def _modules() -> list[Path]:
    found = sorted(p for p in RUNS.rglob("*.py") if p.name != PERMITTED)
    assert found, "no modules found; this test would pass vacuously"
    return found


def _called_names(source: str) -> set[str]:
    """Every name invoked as a call, wherever it appears.

    Walks the whole tree rather than the module body, because a clock read
    inside a function is precisely the shape this test exists to catch, and it
    is the only shape a linter would not already mention.
    """
    called: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called.add(func.attr)
        elif isinstance(func, ast.Name):
            called.add(func.id)
    return called


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_only_identity_may_read_a_clock_or_a_random_source(module: Path) -> None:
    offending = sorted(_called_names(module.read_text(encoding="utf-8")) & FORBIDDEN_CALLS)
    assert not offending, (
        f"{module.name} calls {offending}. Only `{PERMITTED}` may: every other "
        f"module takes `now` and `run_id` as parameters (FR-072), which is what "
        f"keeps the claim policy testable at an arbitrary instant and keeps the "
        f"layers below this one deterministic"
    )


def test_identity_is_where_the_clock_actually_lives() -> None:
    """Guards the guard: the exemption must be exempting something.

    If `identity.py` stopped reading a clock, every module would trivially pass
    the check above and the rule would be enforcing nothing.
    """
    source = (RUNS / PERMITTED).read_text(encoding="utf-8")
    assert _called_names(source) & FORBIDDEN_CALLS, (
        f"{PERMITTED} reads no clock and no random source, so the exemption above "
        "is protecting nothing and the check has gone vacuous"
    )


def test_the_check_finds_a_clock_read_inside_a_function() -> None:
    """The shape that matters: not at module level, where anyone would see it."""
    source = "from datetime import datetime\ndef f():\n    return datetime.now()\n"
    assert _called_names(source) & FORBIDDEN_CALLS == {"now"}


def test_the_check_finds_a_bare_imported_name() -> None:
    """`from time import monotonic` then `monotonic()` has no attribute to match."""
    assert _called_names("from time import monotonic\nx = monotonic()\n") >= {"monotonic"}


def test_the_check_permits_a_type_annotation() -> None:
    """Importing `datetime` to type a field is not reading one."""
    source = "from datetime import datetime\nclass M:\n    at: datetime\n"
    assert not _called_names(source) & FORBIDDEN_CALLS
