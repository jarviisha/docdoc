/** A timestamp as the console shows it: local time, with local and UTC on hover. */

import { bothZones, when } from "../model/format.ts";

export function Time({ iso }: { iso: string | null }) {
  const title = bothZones(iso);
  return (
    <time dateTime={iso ?? undefined} title={title === "" ? undefined : title} className="tabular-nums">
      {when(iso)}
    </time>
  );
}
