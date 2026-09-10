# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists: it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

`CONTEXT.md` does not exist in this repo yet. That is expected, not a gap to report.

## File structure

This is a **single-context** repo. Its actual layout:

```
/
├── CONTEXT.md                         ← not created yet; /domain-modeling adds it lazily
├── docs/
│   ├── adr/                           ← numbered ADRs, 0001- onwards
│   │   ├── 0001-parser-and-ocr-strategy-in-mvp.md
│   │   ├── 0002-blob-and-document-identity.md
│   │   └── README.md
│   └── concepts/                      ← prose explainers per domain concept
│       ├── document.md
│       ├── extraction.md
│       └── grounding.md
├── specs/                             ← feature specs (spec-kit)
└── src/
```

For contrast, a multi-context repo (signalled by a `CONTEXT-MAP.md` at the root) would look like:

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids. Until `CONTEXT.md` exists, `docs/concepts/` carries the working vocabulary — read the relevant file there and use its terms.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (Apache 2 license), but worth reopening because…_
