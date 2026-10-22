/** A section's name within a page: small, uppercase, muted. The top bar names the page. */

import type { ReactNode } from "react";

export function SectionHeading({ children }: { children: ReactNode }) {
  return <h2 className="text-[11px] font-medium tracking-wider text-slate-500 uppercase">{children}</h2>;
}
