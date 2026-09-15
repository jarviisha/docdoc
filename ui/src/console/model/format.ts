/**
 * How identifiers and timestamps read on screen.
 *
 * In the model rather than in a component for the ordinary reason — it is a
 * decision, and decisions here are the only tested surface — but also for a
 * specific one: the first version of the run table printed
 * `2026-09-11T09:17:00.253693+00:00` in a column beside a full UUID, seven rows
 * of it, and the result was unreadable at a glance. What a column shows is a
 * choice about whether the page can be scanned.
 */

/**
 * A timestamp, to the second, in the reader's own zone.
 *
 * Microseconds are precision nobody scanning a list uses; the API keeps them and
 * the detail view can show the raw value. `toLocaleString` because an operator
 * comparing a run to a log line on their own machine is comparing local times.
 */
export function when(iso: string | null): string {
  if (iso === null || iso === "") return "—";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso; // unparseable: show what arrived
  return at.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * The first segment of a UUID, which is what a person uses to tell rows apart.
 *
 * Never the identifier a caller passes anywhere: the full value is on the detail
 * view and in the link. Eight characters, because that is a UUID's own first
 * group and truncating on a boundary somebody recognises reads as an
 * abbreviation rather than as corruption.
 */
export function shortId(id: string): string {
  const [first] = id.split("-");
  return first === undefined || first.length === 0 ? id : first;
}

/**
 * A content-addressed identity, shortened at both ends.
 *
 * `sha256:358dcfa3…0fe8ef06`. The prefix says which algorithm, and the tail is
 * what distinguishes two digests a reader is comparing by eye — a head-only
 * truncation hides exactly the half that differs when somebody is checking
 * whether two rows name the same document.
 */
export function shortDigest(digest: string): string {
  const [algorithm, value] = digest.split(":");
  if (value === undefined || value.length <= 16) return digest;
  return `${algorithm}:${value.slice(0, 8)}…${value.slice(-8)}`;
}
