-- Run timeline: `started_at` and a content-free `error_reason`
-- (docs/superpowers/plans/2026-10-20-console-monitoring.md, spec decision 7).
--
-- `started_at` is the most recent claim, set every time a claim statement
-- moves a run to `running` -- including a redelivery, which is why it is "most
-- recent" and not "first". `error_reason` is a member of the failing error's own
-- `REASONS` frozenset, never its message (ADR-0023, FR-037's no-content rule).
--
-- Idempotent, like every migration before it: a deployment pipeline re-runs it.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS error_reason text;

-- What the console's Overview and recent-runs reads filter on: a run's terminal
-- age. Partial, on the three terminal statuses only, so a queue of `queued` and
-- `running` rows -- which this index has no opinion about -- never bloats it.
CREATE INDEX IF NOT EXISTS runs_terminal_recent
    ON runs (updated_at)
    WHERE status IN ('succeeded', 'failed', 'cancelled');
