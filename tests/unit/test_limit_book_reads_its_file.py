"""``LimitBook.from_file`` — the per-tenant override file, read strictly.

**Every path through this was untested.** `PostgresLimiter` has its own
integration coverage, but the thing that decides *which* numbers it enforces —
the file an operator writes, its inheritance rule, and its four refusals — was
executed by nothing. That is the wrong half to leave unverified: a limiter that
counts correctly against the wrong policy refuses the wrong customer, and a file
that fails to load quietly is a deployment that believes it is protected.

Strict for the reason `KeyRing.from_file` is. The quiet alternative — skip the
entry that will not parse and start anyway — serves traffic with one customer's
cap silently absent, which is indistinguishable from working until it is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdoc.runs.limits import LimitBook, LimitPolicy

#: What the deployment configured before it read any file. A tenant with no
#: entry gets this, and so does a field an entry omits.
DEFAULT = LimitPolicy(submissions_per_minute=60, concurrent_runs=4)


def _book(tmp_path: Path, raw: object, *, default: LimitPolicy = DEFAULT) -> LimitBook:
    path = tmp_path / "limits.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return LimitBook.from_file(path, default)


# -- what it reads -----------------------------------------------------------


def test_a_tenant_with_no_entry_gets_the_default(tmp_path: Path) -> None:
    book = _book(tmp_path, {"tenants": {"acme": {"concurrent_runs": 1}}})

    assert book.policy_for("globex") == DEFAULT


def test_an_omitted_field_inherits_and_is_not_zero(tmp_path: Path) -> None:
    """**Absent is not zero.** It means the limit does not exist for that tenant.

    A reading that treated the missing key as `0` would refuse every submission
    from a tenant who was granted a *larger* concurrency and nothing else.
    """
    book = _book(tmp_path, {"tenants": {"acme": {"concurrent_runs": 16}}})

    acme = book.policy_for("acme")

    assert acme.concurrent_runs == 16
    assert acme.submissions_per_minute == DEFAULT.submissions_per_minute
    assert acme.runs_per_period is None


def test_the_files_default_replaces_the_configured_one(tmp_path: Path) -> None:
    """A `default` block in the file is the deployment's default, and the
    argument is only what applies when the file names none."""
    book = _book(tmp_path, {"default": {"submissions_per_minute": 5}})

    assert book.policy_for("anyone").submissions_per_minute == 5


def test_an_empty_default_block_leaves_the_configured_one_alone(tmp_path: Path) -> None:
    """`{}` configures nothing, which is not the same as configuring zero."""
    book = _book(tmp_path, {"default": {}, "tenants": {}})

    assert book.policy_for("anyone") == DEFAULT


def test_a_priority_ceiling_is_accepted_and_is_not_a_limit(tmp_path: Path) -> None:
    """FR-087b. It lives in this file and is read by the HTTP layer.

    Deliberately not a `LimitPolicy` field: nothing counts it and nothing
    refuses on it, so giving the model a fifth attribute the limiter never reads
    would be a second source of truth. Accepted here so a file carrying one is
    not rejected as a typo.
    """
    book = _book(tmp_path, {"tenants": {"acme": {"priority_ceiling": 5, "concurrent_runs": 2}}})

    assert book.policy_for("acme").concurrent_runs == 2


def test_configured_is_true_when_any_tenant_has_a_limit(tmp_path: Path) -> None:
    """The switch that decides whether a limiter is built at all (FR-049)."""
    nothing = _book(tmp_path, {}, default=LimitPolicy())
    something = _book(
        tmp_path, {"tenants": {"acme": {"concurrent_runs": 1}}}, default=LimitPolicy()
    )

    assert nothing.configured is False
    assert something.configured is True


# -- what it refuses ---------------------------------------------------------


def test_a_misspelled_limit_is_refused_and_named(tmp_path: Path) -> None:
    """The refusal this file exists for.

    A misspelled limit is a limit that is not applied. Silence about it is how a
    deployment believes it is protected and is not, so the message has to carry
    the name that was not understood.
    """
    with pytest.raises(ValueError, match="concurent_runs"):
        _book(tmp_path, {"tenants": {"acme": {"concurent_runs": 2}}})


def test_a_file_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be an object"):
        _book(tmp_path, [{"acme": {}}])


def test_a_tenants_key_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="`tenants` must be an object"):
        _book(tmp_path, {"tenants": ["acme"]})


def test_an_entry_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not an object"):
        _book(tmp_path, {"tenants": {"acme": 12}})


def test_a_file_that_is_not_json_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "limits.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        LimitBook.from_file(path, DEFAULT)


def test_a_file_that_cannot_be_read_names_the_error_class(tmp_path: Path) -> None:
    """The class and not the message: an OS error quotes a path, and a path on
    an operator's machine is not something to echo into a log verbatim."""
    with pytest.raises(ValueError, match="cannot be read: "):
        LimitBook.from_file(tmp_path / "absent.json", DEFAULT)
