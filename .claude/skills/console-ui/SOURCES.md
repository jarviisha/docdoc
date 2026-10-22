# Sources

Open-source consoles studied on 2026-10-22 for [SKILL.md](SKILL.md). Patterns were read from source, not screenshots.

**The licence decides what may be taken.** docdoc ships Apache-2.0.
- **MIT or Apache-2.0:** ideas, and short snippets with attribution.
- **AGPL, SSPL or FSL:** layout and behaviour ideas only. Never code, class lists, tokens or copy text.

| Repo | Licence | Worth studying for |
|---|---|---|
| `triggerdotdev/trigger.dev` (`apps/webapp`) | Apache-2.0 | Runs table, mono ids with copy (`TruncatedCopyableValue`), status as an icon plus label, `DateTime` with a UTC/local tooltip, cancel dialog ("Cancel this run?" / "Cancel run" / "Canceling…") |
| `temporalio/ui` | MIT | 32px rows with zebra, a refresh bar inside the header instead of a spinner, uppercase bordered status badges, a detail header (back link, status, copyable id), a key-value `dl` grid, "Type DELETE to confirm" |
| `supabase/supabase` (`apps/studio`) | Apache-2.0 | Uppercase mono table headers, a typed-name confirm with a consequence admonition, uppercase nav group titles, empty states inside the table card |
| `shadcn-ui/ui` (`apps/v4`, tasks example, `dashboard-01`) | MIT | Data-table toolbar (h-8 controls, faceted filters, reset), pagination, the `Field` and `Empty` primitives |
| `satnaing/shadcn-admin` | MIT | The same toolbar in a full app, a `ConfirmDialog` with `isLoading`, tinted outline status badges |
| `langfuse/langfuse` (`web/`) | MIT, except `ee/` dirs | A 13px `text-sm` scale, a row-height switch (28px small), a side "peek" sheet for detail, compact durations (`2m 05s`) |
| `inngest/inngest` (`ui/`) | SSPL (patterns only) | Compact density (h-8 rows, h-7 header), a timestamp copy-on-hover, a run-detail split pane, a `dl` metadata card |
| `unkeyed/unkey` (`apps/dashboard`) | FSL-1.1 (patterns only) | The show-once secret dialog (masked, copy, "won't be shown again", warn on close), typed delete confirm, a timestamp tooltip with UTC/local/relative |
| `openstatusHQ/openstatus` | AGPL-3.0 (patterns only) | Per-cell skeletons, a compact pagination bar, dashed empty-state blocks |
| `documenso/documenso` | AGPL-3.0 (patterns only) | Skeleton rows, an empty state with a clear-filters action |
| `Zipstack/unstract` (`frontend/`) | AGPL-3.0 (patterns only) | Where the console is heading: Prompt Studio (prompt cards beside a PDF viewer with source highlights), adapter settings forms rendered from JSON Schema (`@rjsf`) |

## Where they agree, and where docdoc departs

- **Agreement:**
  - Dense rows of 28–36px.
  - Muted small headers.
  - Mono, truncated, copyable ids.
  - Absolute time with a UTC/local tooltip.
  - Right-aligned `tabular-nums` numbers.
  - Status carries a word as well as a colour.
  - Destructive confirms name the verb and the object, and the riskiest ones demand typed text.
  - Empty states give one sentence and at most one action.
- **docdoc departs** on purpose:
  - 2px radius and no shadows. Every source uses 6–10px radii, and most have shadowed dialogs.
  - Uppercase table headers, as in Supabase and Temporal only.
  - Outlined uppercase status tags, as in Temporal, rather than dots.
- **Not adopted yet**, worth considering when the need appears:
  - Faceted multi-select filters with counts (shadcn tasks).
  - A row-click side peek (Langfuse).
  - A split-pane run detail (Trigger, Inngest).
  - A time-range filter.
