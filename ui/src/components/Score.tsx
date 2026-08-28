/**
 * A score, always with the tier it belongs to.
 *
 * An exact score is `1.0` by definition; a fuzzy score is a measured similarity.
 * They are not comparable, and ADR-0004 says ranking across them is meaningless
 * — so there is no shared bar, no shared scale, and no sort control. The tier is
 * rendered as text beside the number because a number alone is an invitation to
 * compare (FR-020).
 *
 * A `Badge` and not a `ProgressBar`, deliberately. A bar implies a common scale,
 * which is precisely the reading ADR-0004 forbids, and Astryx has one that would
 * have looked like the obvious component to reach for.
 */

import { Badge } from "@astryxdesign/core";

import type { ScoreView } from "../model/types.ts";

export function Score({ score }: { score: ScoreView | null }) {
  if (score === null) return null;

  return <Badge label={`${score.tier} ${score.value.toFixed(2)}`} />;
}
