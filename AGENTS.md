# AGENTS.md

## Agent skills

### Issue tracker

Issues live in the `jarviisha/docdoc` GitHub Issues, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label named after its role. See `docs/agents/triage-labels.md`.

### Running tests

While iterating, run only the tests for what you changed: `uv run pytest -n0 tests/path/test_x.py`. `-n0` skips the parallel workers, which cost more than they save on a few files. Run the whole suite once, with `make test`, before reporting the work done. Don't pass `-o addopts=""`: it also turns off parallelism.

### Domain docs

Single-context: one `CONTEXT.md` at the repo root plus `docs/adr/`. See `docs/agents/domain.md`.

## House style

This repo follows jarviisha/house-style (https://github.com/jarviisha/house-style),
adopted on 2026-10-24 in #41. Claude: use the house-style skill.

- Squash merge only. PR title = commit title: `type(scope): summary`.
- PR body is plain text: short paragraphs and `-` bullets, no `##` headings,
  no emoji, no "Generated with" line. It becomes the squash commit body,
  so it must describe all the work done in the PR.
- Branches: `type/short-slug`.
- Workflows: ci.yml, release.yml, deploy.yml.
- Releases: tag vX.Y.Z, release title = tag, body = CHANGELOG section.
- Everything in English.

History before 2026-10-24 does not follow these rules. Do not copy the style
of older commits, PR bodies, branches or releases, and do not rewrite or
rename them.
