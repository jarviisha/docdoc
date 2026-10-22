# AGENTS.md

## Agent skills

### Issue tracker

Issues live in the `jarviisha/docdoc` GitHub Issues, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label named after its role. See `docs/agents/triage-labels.md`.

### Pull requests

PRs merge by squash only. The squash commit takes the PR title as its subject and the PR body as its message, so the body must describe all the work done in the PR.

### Running tests

While iterating, run only the tests for what you changed: `uv run pytest -n0 tests/path/test_x.py`. `-n0` skips the parallel workers, which cost more than they save on a few files. Run the whole suite once, with `make test`, before reporting the work done. Don't pass `-o addopts=""`: it also turns off parallelism.

### Domain docs

Single-context: one `CONTEXT.md` at the repo root plus `docs/adr/`. See `docs/agents/domain.md`.
