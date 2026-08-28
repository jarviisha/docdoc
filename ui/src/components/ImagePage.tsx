/**
 * One image, drawn at its own aspect ratio, with the overlay on top.
 *
 * The counterpart to `Page.tsx` for the two image types the picker offers and
 * pdf.js cannot open. An image document has exactly one page and needs no
 * page-splitting (spec §Edge Cases), so there is no viewport, no scale, and no
 * page number — which is most of what `Page.tsx` is.
 *
 * **No rotation here either, and for the same reason.** The parsers resolve
 * orientation before normalizing, and the `gcv` adapter — the one that accepts
 * these types — records `rotation = 0` because its service reports displayed
 * coordinates already (research R1). The browser draws the image as its own
 * metadata orients it, which is the space those coordinates are in.
 *
 * The container's height is left to the image rather than measured, so the
 * percentage coordinates the overlay uses resolve against the drawn size
 * whatever that turns out to be. `Page.tsx` cannot do this because a canvas has
 * no intrinsic size to lay out from.
 *
 * The object URL is revoked on unmount. It is an in-memory handle to bytes the
 * page already holds, not a copy at rest — nothing here writes to storage, and
 * `check-readonly.mjs` fails the build if it ever does (FR-032).
 */

import { useEffect, useState } from "react";

interface Props {
  bytes: Uint8Array;
  mediaType: string;
  width: number;
  children?: React.ReactNode;
}

export function ImagePage({ bytes, mediaType, width, children }: Props) {
  const [source, setSource] = useState<string | null>(null);

  useEffect(() => {
    const url = URL.createObjectURL(new Blob([bytes.slice()], { type: mediaType }));
    setSource(url);
    return () => {
      URL.revokeObjectURL(url);
      setSource(null);
    };
  }, [bytes, mediaType]);

  if (source === null) return null;

  return (
    <div style={{ position: "relative", width, marginBottom: "1rem" }}>
      <img src={source} alt="" style={{ display: "block", width: "100%" }} />
      {children}
    </div>
  );
}
