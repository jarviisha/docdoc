/**
 * The authoritative surface. Every fact is here.
 *
 * Losing the overlay must lose the picture and no fact (FR-055, FR-058), so this
 * list carries the field, the value, the verdict, the grounding status, the
 * geometry state and the pages — for every value, whether or not a rectangle
 * exists for it. An ungrounded value appears exactly as prominently as a located
 * one (FR-016, FR-017): the promise is "never a guess", and a viewer that
 * quietly dropped what it could not draw would break that promise more
 * effectively than any bug.
 *
 * Every distinction is carried by **text supplied by the view model**, never by
 * colour alone (FR-057). `Badge` renders that text with a colour beside it; the
 * text is what carries the meaning, and the colour is decoration on top of it.
 *
 * Rows are `SelectableCard`s, so selection is a real control with real keyboard
 * behaviour rather than a `div` with a click handler (FR-056).
 */

import { Badge, SelectableCard, Stack, Text } from "@astryxdesign/core";

import type { ValueRow } from "../model/types.ts";
import { Score } from "./Score.tsx";

interface Props {
  values: ValueRow[];
  selection: string | null;
  onSelect: (fieldPath: string) => void;
}

export function ValueList({ values, selection, onSelect }: Props) {
  return (
    <Stack direction="vertical" gap={2}>
      {values.map((row) => (
        <SelectableCard
          key={row.fieldPath}
          label={row.fieldPath}
          isSelected={selection === row.fieldPath}
          onChange={() => onSelect(row.fieldPath)}
        >
          <Stack direction="vertical" gap={1}>
            <Text size="sm" weight="bold">
              {row.fieldPath}
            </Text>
            <Text size="base">{row.value ?? "—"}</Text>
            <Stack direction="horizontal" gap={1}>
              {/* Text, not colour. The three geometry states and the three
                  grounding statuses each carry their own sentence from the
                  model, so a reader who cannot distinguish two shades loses
                  nothing (FR-057). */}
              <Badge label={row.labels.status} />
              <Badge label={row.labels.geometry} />
              <Badge label={`verdict ${row.labels.verdict}`} />
              {row.pages.length > 0 ? (
                <Badge label={`page ${row.pages.map((page) => page + 1).join(", ")}`} />
              ) : null}
              <Score score={row.score} />
            </Stack>
          </Stack>
        </SelectableCard>
      ))}
    </Stack>
  );
}
