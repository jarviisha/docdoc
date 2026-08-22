"""The layer chain, checked by a test as well as by `lint-imports`.

The constitution requires the dependency direction to be enforced by an automated
test, not by convention, and `import-linter` already does that in CI. This is not
a duplicate of it: it checks the two things a contract file cannot.

**That the contract still describes reality.** A layers list is a claim about
which packages exist and in what order. If a package is renamed, or a milestone
adds one and forgets the contract, `lint-imports` keeps passing on the layers it
was told about and says nothing about the one it was not.

**That the constitution and the contract agree.** Principle X says the
`pyproject.toml` contract is the authoritative form and that the prose must be
amended in the same change. From Milestone 4 to Milestone 6 the two disagreed and
nobody noticed, because nothing compared them.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The chain, in the order Principle X states it. `api` and `cli` share the top
#: position because neither may import the other.
EXPECTED_LAYERS = [
    "docdoc.api : docdoc.cli",
    "docdoc.recording",
    "docdoc.evaluation",
    "docdoc.pipeline",
    "docdoc.validation",
    "docdoc.grounding",
    "docdoc.extraction",
    "docdoc.ingest",
    "docdoc.artifacts",
    "docdoc.kernel",
]


def _config() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _contracts() -> list[dict[str, object]]:
    return _config()["tool"]["importlinter"]["contracts"]  # type: ignore[index,return-value]


def _contract(kind: str) -> dict[str, object]:
    for contract in _contracts():
        if contract.get("type") == kind:
            return contract
    pytest.fail(f"no {kind!r} contract is configured")


def test_the_enforced_chain_is_the_one_principle_x_states() -> None:
    assert _contract("layers")["layers"] == EXPECTED_LAYERS


def test_every_layer_named_in_the_contract_exists() -> None:
    """A contract naming a package that is gone passes vacuously."""
    for entry in EXPECTED_LAYERS:
        for name in (part.strip() for part in entry.split(":")):
            package = ROOT / "src" / Path(*name.split("."))
            assert (package / "__init__.py").is_file(), f"{name} is in the contract and not on disk"


def test_every_package_on_disk_is_named_in_the_contract() -> None:
    """The failure mode the contract cannot catch: a layer nobody declared.

    A new package that no layer names is unconstrained — it may import anything,
    in any direction, and CI stays green.
    """
    declared = {
        name.strip() for entry in EXPECTED_LAYERS for name in entry.split(":")
    }
    on_disk = {
        f"docdoc.{path.name}"
        for path in (ROOT / "src" / "docdoc").iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    assert on_disk == declared, f"undeclared packages: {sorted(on_disk - declared)}"


def test_the_two_front_ends_are_independent() -> None:
    """A shared layer position says nothing about importing each other."""
    contract = _contract("independence")
    assert set(contract["modules"]) == {"docdoc.api", "docdoc.cli"}  # type: ignore[arg-type]


def test_the_constitution_states_the_same_chain() -> None:
    """Principle X's prose against the contract CI checks.

    The specific failure this prevents: the two drifted for three milestones,
    and the reconciliation lived in a `pyproject.toml` comment and three
    `research.md` files. A dependency graph a reader must reconstruct from three
    research documents is not a governing one.
    """
    text = (ROOT / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8")

    principle = text.split("### X. Layered Dependency Direction")[1].split("### XI.")[0]
    chain = re.search(r"```text\n(.*?)```", principle, re.DOTALL)
    assert chain is not None, "Principle X no longer states a chain"

    stated = [
        name.strip().lower()
        for name in re.split(r"[,→\n]", chain.group(1))
        if name.strip()
    ]
    enforced = [
        name.strip().removeprefix("docdoc.")
        for entry in EXPECTED_LAYERS
        for name in entry.split(":")
    ]
    assert stated == enforced


def test_the_deterministic_layers_cannot_reach_the_store_or_a_framework() -> None:
    """Grounding and validation do no I/O and know no transport.

    `docdoc.artifacts` sits *below* them in the chain, so the layers contract
    permits the import — only this forbids it. Without it "grounding is
    deterministic all the way down" would be a sentence rather than a build
    failure.
    """
    for source in ("docdoc.grounding", "docdoc.validation", "docdoc.evaluation"):
        forbidden = next(
            contract["forbidden_modules"]
            for contract in _contracts()
            if contract.get("type") == "forbidden" and contract["source_modules"] == [source]
        )
        assert "docdoc.artifacts" in forbidden, f"{source} may reach the store"
        assert "fastapi" in forbidden, f"{source} may reach a web framework"


def test_the_command_line_needs_no_dependency() -> None:
    """The whole argument for argparse: `docdoc` belongs to the base install."""
    extras = _config()["project"]["optional-dependencies"]  # type: ignore[index]
    assert "cli" not in extras, "a cli extra would put the command behind a second install"
    assert _config()["project"]["scripts"] == {"docdoc": "docdoc.cli:main"}  # type: ignore[index]


def test_the_http_interface_stays_behind_an_extra() -> None:
    extras = _config()["project"]["optional-dependencies"]  # type: ignore[index]
    assert any("fastapi" in entry for entry in extras["api"])
    base = _config()["project"]["dependencies"]  # type: ignore[index]
    assert not any("fastapi" in entry for entry in base)
