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
    declared = {name.strip() for entry in EXPECTED_LAYERS for name in entry.split(":")}
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

    stated = [name.strip().lower() for name in re.split(r"[,→\n]", chain.group(1)) if name.strip()]
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


# -- FR-030: the command line contains no stage logic ------------------------

#: The three stage entry points the CLI must reach only through the pipeline.
#: Names rather than modules, because a module-level ban would be wrong: the CLI
#: legitimately imports ``ExtractionResult``, ``GroundingResult``, and
#: ``ValidationResult`` from these same modules to *render* a result, which is
#: precisely what FR-030 permits — "it parses arguments, calls the pipeline, and
#: formats a result".
_STAGE_ENTRY_POINTS = {
    "docdoc.extraction.extract": {"extract"},
    "docdoc.grounding": {"ground"},
    "docdoc.grounding.ground": {"ground"},
    "docdoc.validation": {"validate"},
}

#: ``docdoc.ingest.parse`` is deliberately absent from the set above. ``docdoc
#: parse FILE`` is one of the six commands FR-026 names, and there is no pipeline
#: entry point for a bare parse — the pipeline runs four stages or none. So the
#: parse command calls ``ingest.parse`` directly, and that is the front end doing
#: its job rather than containing a stage's logic.
_PARSE_IS_A_COMMAND = "docdoc.ingest"


def _cli_modules() -> list[Path]:
    root = Path("src/docdoc/cli")
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", _cli_modules(), ids=lambda p: p.name)
def test_the_command_line_reaches_no_stage_except_through_the_pipeline(path: Path) -> None:
    """FR-030, machine-checked instead of asserted in prose.

    ``contracts/cli.md`` §6 says "a behaviour reachable only through the command
    line is a bug", and until now nothing checked it. The danger is not
    hypothetical: a command that called ``extract()`` and then ``ground()``
    itself would be a *second* definition of the stage order, which is the exact
    condition FR-009 exists to prevent and which SC-014 asserts is expressed in
    exactly one place.

    An ``import-linter`` contract cannot say this, because it works at module
    granularity and the CLI must keep importing result models from the same
    modules. So this reads the imports by name, the way
    ``test_kernel_purity.py`` reads the kernel's.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        forbidden = _STAGE_ENTRY_POINTS.get(node.module, set())
        imported = {alias.name for alias in node.names}
        overlap = forbidden & imported
        assert not overlap, (
            f"{path} imports {sorted(overlap)} from {node.module}. The command "
            "line must reach a stage only through docdoc.pipeline (FR-030) — "
            "otherwise the stage order has a second definition, which is what "
            "FR-009 and SC-014 exist to prevent. Importing a *result model* from "
            "the same module is fine and is what formatting a result needs."
        )


def test_the_parse_command_may_call_ingest_directly() -> None:
    """The deliberate exception, asserted so it stays deliberate.

    ``docdoc parse`` is one of the six commands FR-026 names and the pipeline has
    no entry point for a bare parse — it runs four stages or none. If this ever
    fails because the exception was removed, the parse command needs somewhere
    else to go before the rule above tightens.
    """
    assert _PARSE_IS_A_COMMAND not in _STAGE_ENTRY_POINTS
    parse_command = Path("src/docdoc/cli/commands/parse.py")
    assert "from docdoc.ingest import parse" in parse_command.read_text(encoding="utf-8")
