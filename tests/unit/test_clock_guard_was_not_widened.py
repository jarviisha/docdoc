"""The guard that watches the guard (FR-096a, T026b).

`tests/unit/test_runs_clock_confinement.py` permits exactly one module in
`docdoc.runs` to read a clock or a random source. Milestone 10 adds seven modules
to that package and every one of them wants an identifier, an instant, or a
secret — so the cheapest way to keep the suite green is to add names to that
file's `PERMITTED` set.

That change is forbidden by FR-096a, and the reason is written in the guard's own
docstring. An earlier version of it matched bare call names and `worker.py` passed
by importing `now as clock`: *"The alias was written to avoid a false positive and
it also disabled the check, which is the precise failure mode a guard that cannot
see provenance will always have."*

A guard that can be widened by the change it was watching for is not a guard, so
this file makes the widening itself fail. It is deliberately small and deliberately
literal: it reads the constant, not the behaviour.

**If this fails, the fix is not here.** It is to give `docdoc.runs.identity` the
allocator the new module needs and to pass the value downward as a parameter,
exactly as `queue.py` does — which is also what keeps `pipeline` and everything
below it a pure function of its inputs, and therefore what keeps ADR-0013 §4's
redelivery argument true.
"""

from __future__ import annotations

from tests.unit.test_runs_clock_confinement import FORBIDDEN_MODULES, PERMITTED


def test_only_identity_may_read_a_clock_or_a_random_source() -> None:
    """`PERMITTED` names one module, and it is `identity.py`."""
    assert PERMITTED == "identity.py", (
        "the clock-confinement guard has been widened. FR-096a forbids a second "
        "entry here: every module in `docdoc.runs` takes instants and identifiers "
        "as parameters, and `identity.py` is where they are allocated. Add the "
        "allocator there instead."
    )


def test_the_forbidden_set_still_covers_every_ambient_source() -> None:
    """Narrowing the set is the other way to disable the guard.

    Widening `PERMITTED` is the obvious edit; dropping `uuid` or `secrets` from
    `FORBIDDEN_MODULES` is the quiet one, and it would let a credential generator
    or a delivery identity be allocated anywhere in the package with CI green.
    """
    assert {"datetime", "time", "uuid", "random", "secrets"} <= FORBIDDEN_MODULES
