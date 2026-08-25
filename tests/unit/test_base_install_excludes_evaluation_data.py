"""T091 — the base install carries no golden set and no new dependency (FR-059).

FR-059 currently holds **by build configuration rather than by rule**, and that is
exactly the kind of guarantee that erodes silently. Nothing today stops somebody
adding ``datasets`` to the wheel's packages, or reaching for a plotting library
to render a report, and the failure is invisible: the wheel gets bigger, the
install grows a dependency, and the only person who notices is a user wondering
why a document-processing library ships invoices.

Two properties, checked against ``pyproject.toml`` and against the import graph:

**``datasets/`` is not in the wheel.** It is evaluation data, useful in a
checkout and pointless in a deployment. Shipping it would also mean shipping
documents — synthetic here, but the *shape* is the thing: a library that vendors
a corpus invites the next dataset to be a real one.

**Importing the scorer pulls in no provider SDK and no new runtime dependency.**
The base install is ``pydantic`` plus ``rapidfuzz``, and this milestone added
neither a third nor an optional extra that quietly became required.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

PYPROJECT = pathlib.Path("pyproject.toml")


def _config() -> dict:  # type: ignore[type-arg]
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_the_dataset_directory_exists_to_be_excluded() -> None:
    """The guard on the guard: excluding a directory that does not exist proves nothing."""
    assert pathlib.Path("datasets/mvp/manifest.json").is_file()


def test_datasets_is_outside_the_wheel() -> None:
    """The wheel ships ``src/docdoc`` and nothing else."""
    packages = _config()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages == ["src/docdoc"], (
        f"the wheel ships {packages}. Evaluation data belongs in a checkout, not in "
        "an install (FR-059)"
    )
    assert not any("dataset" in entry for entry in packages)


def test_the_runtime_dependencies_are_unchanged() -> None:
    """Milestone 6 added no runtime dependency, and this is where that stays true.

    Pinned as a set rather than "at most N", because the useful question is not
    how many there are but which ones — a swap would leave a count check green.
    """
    dependencies = _config()["project"]["dependencies"]
    names = {entry.split(">")[0].split("=")[0].split("[")[0].strip() for entry in dependencies}

    assert names == {"pydantic", "rapidfuzz"}, (
        f"the base install now requires {sorted(names)}. Scoring is a deterministic "
        "computation over recorded facts; it needs nothing a document parser does not"
    )


def test_no_new_optional_extra_was_added_for_evaluation() -> None:
    """An ``[evaluation]`` extra would make the feature opt-in, which it is not.

    Scoring is available to every install, because a quality mechanism that has to
    be installed separately is one most deployments will not have.
    """
    extras = set(_config()["project"].get("optional-dependencies", {}))

    # `api` was added by Milestone 7 for the HTTP interface, which is genuinely
    # optional — the library and the command line are complete without it. The
    # claim this test makes is unchanged and still holds: there is no
    # `evaluation` extra, and none of the others gates scoring. Note also the
    # absence of a `cli` extra, which is not an oversight: the command line is
    # argparse, so there is nothing to keep out of the base install.
    assert extras == {"pdf", "azure", "google", "api", "dev"}, (
        f"the extras are now {sorted(extras)}; evaluation must need none of its own"
    )


def test_importing_the_scorer_pulls_in_no_provider_sdk() -> None:
    """Run in a fresh interpreter, because this one has imported half the repository.

    Checking ``sys.modules`` in-process would pass on a machine where a test
    imported ``google.genai`` earlier and fail on one where it did not, which is
    the least useful kind of check.
    """
    probe = (
        "import sys; sys.path.insert(0, 'src');"
        "import docdoc.evaluation;"
        "bad=[m for m in sys.modules if m.split('.')[0] in "
        "{'google','openai','anthropic','azure','boto3','httpx','requests','pymupdf','fitz'}];"
        "print(sorted(bad))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing docdoc.evaluation loaded {result.stdout.strip()}. A provider SDK in "
        "the scorer's import graph is the FR-007 contract broken, and it means the "
        "base install now depends on one"
    )


def test_importing_the_recorder_is_where_the_pipeline_arrives() -> None:
    """The contrast, so the check above is not read as "nothing imports anything".

    ``docdoc.recording`` is the layer that runs the pipeline, so it reaches the
    stages -- and that asymmetry is the whole design: producing a prediction set
    needs a provider, scoring one must not.
    """
    probe = (
        "import sys; sys.path.insert(0, 'src');"
        "import docdoc.recording;"
        "print('docdoc.evaluation' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_the_dataset_is_reachable_from_a_checkout_and_not_from_a_package_path() -> None:
    """``datasets/`` is repository data, addressed by path rather than by import.

    A ``docdoc.datasets`` package would be the first step toward shipping it, and
    would make the exclusion above a configuration detail somebody could
    reasonably "fix".
    """
    assert not pathlib.Path("src/docdoc/datasets").exists()

    import docdoc.evaluation

    assert not hasattr(docdoc.evaluation, "DATASET_ROOT"), (
        "the package points at a dataset location, which couples the library to "
        "data it does not ship"
    )
