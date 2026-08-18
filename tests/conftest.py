"""Shared test configuration.

Two concerns: Hypothesis profiles, and keeping the offline suite hermetic against
whatever configuration the developer's shell happens to carry.

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

import pytest
from hypothesis import HealthCheck, Verbosity, settings

#: Credential names any adapter or parser reads. Cleared for the offline suite for
#: the same reason as the ``DOCDOC_*`` names below: a contributor who *has*
#: credentials must get the same result as one who does not (SC-019).
CREDENTIAL_ENV = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "DOCDOC_AZURE_DI_ENDPOINT",
    "DOCDOC_AZURE_DI_KEY",
)


@pytest.fixture(autouse=True)
def _hermetic_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear docdoc configuration and credentials for every offline test.

    SC-019 says a contributor runs 100% of the unit and property suites. A suite
    whose result depends on the developer's shell does not meet that, and it fails
    in the worst possible way -- on the machine that is *correctly* configured for
    real use, while passing everywhere else.

    That is not hypothetical. Milestone 3 introduced ``DOCDOC_GEMINI_MODEL``,
    ``DOCDOC_MODEL_ADAPTERS``, and ``DOCDOC_SCHEMA_PATHS``, guarded the two test
    files it happened to be editing, and left ``test_gemini_mapping.py`` asserting
    ``adapter.model_id == DEFAULT_MODEL`` -- which a set ``DOCDOC_GEMINI_MODEL``
    turns red. Per-file ``delenv`` was the right local fix and is the wrong general
    one: the next variable added would miss the next file, which is how this got
    here in the first place.

    Prefix-matching ``DOCDOC_`` rather than listing names is deliberate for the same
    reason -- a list would need editing every time configuration grows, and the
    editing is the step that gets skipped.

    **Provider-marked tests are exempt**, because ambient configuration is their
    input rather than their contamination: a live test with no credential has
    nothing to do. They skip themselves with a stated reason when it is absent
    (FR-045).
    """
    if request.node.get_closest_marker("provider"):
        return
    for name in [key for key in os.environ if key.startswith("DOCDOC_")]:
        monkeypatch.delenv(name, raising=False)
    for name in CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)


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
