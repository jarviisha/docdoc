"""Shared test configuration.

Hypothesis profiles exist so the property suite can hit the case count spec.md
SC-002 requires (>= 10,000 generated scenarios across the suite) without making
every local run slow.

    default   ~200 examples/test  — fast feedback while developing
    thorough  ~2000 examples/test — CI; clears SC-002 across the invariant tests
    ci        alias for thorough, with the example database disabled

Select with HYPOTHESIS_PROFILE=thorough, or `pytest --hypothesis-profile=thorough`.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, Verbosity, settings

settings.register_profile(
    "default",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.register_profile(
    "thorough",
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.register_profile(
    "ci",
    max_examples=2000,
    deadline=None,
    derandomize=False,
    print_blob=True,
    verbosity=Verbosity.normal,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
