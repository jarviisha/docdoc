/**
 * The rectangles, drawn where the run said they are.
 *
 * Coordinates arrive normalized to `0..1` with a top-left origin, which is
 * already the coordinate system of a CSS box — so the only transformation is
 * multiplying by 100 to make a percentage, and there is deliberately nothing
 * else (FR-023). Every box a value carries is drawn, not the first: a value
 * wrapping across two lines has two, and drawing one is a wrong answer that
 * looks like a right one (FR-015).
 *
 * This component decides nothing. Which boxes exist, which page they belong to
 * and which field they name were all settled by the view model (FR-043).
 */

import type { BoxEntry } from "../model/types.ts";

interface Props {
  boxes: BoxEntry[];
  selection: string | null;
  onSelect: (fieldPath: string) => void;
}

export function Overlay({ boxes, selection, onSelect }: Props) {
  return (
    <>
      {boxes.map((box, index) => {
        const selected = selection === box.fieldPath;
        return (
          <button
            key={`${box.fieldPath}-${index}`}
            type="button"
            aria-label={`Located value for ${box.fieldPath}`}
            onClick={() => onSelect(box.fieldPath)}
            style={{
              position: "absolute",
              left: `${box.x0 * 100}%`,
              top: `${box.y0 * 100}%`,
              width: `${(box.x1 - box.x0) * 100}%`,
              height: `${(box.y1 - box.y0) * 100}%`,
              background: selected ? "rgba(56,132,255,0.28)" : "rgba(56,132,255,0.12)",
              border: selected ? "2px solid #1d4ed8" : "1px solid #60a5fa",
              padding: 0,
              cursor: "pointer",
            }}
          />
        );
      })}
    </>
  );
}
