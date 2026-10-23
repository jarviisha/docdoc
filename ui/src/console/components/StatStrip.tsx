/**
 * The four counts as one bordered strip: on Overview, and above the run list.
 *
 * `LiveStatStrip` reads the overview itself for the run list. It is a glance,
 * not a report: until an answer arrives, or when one fails, it shows nothing,
 * and the list below it says what went wrong with the request that matters.
 */

import { cn } from "@/lib/utils";

import { cardTone, toOverview, type OverviewCards } from "../model/overview.ts";
import { POLL_MS } from "../model/polling.ts";
import { useIntent } from "./useIntent.ts";

const CARDS: { name: keyof OverviewCards; label: string }[] = [
  { name: "queued", label: "Queued" },
  { name: "running", label: "Running" },
  { name: "failed", label: "Failed · last hour" },
  { name: "done", label: "Done · last hour" },
];

export function StatStrip({ cards }: { cards: OverviewCards }) {
  return (
    <div className="flex divide-x rounded-sm border">
      {CARDS.map(({ name, label }) => {
        const split = cards[name];
        return (
          <div key={name} className="flex-1 px-3 py-2">
            <p className="text-[11px] text-slate-500">{label}</p>
            <p
              className={cn(
                "text-base font-semibold tabular-nums",
                cardTone(name, split) === "danger" && "text-red-600",
              )}
            >
              {split.total}
            </p>
            <p className="text-[11px] text-slate-500">
              OCR {split.ocr} · Extraction {split.extraction}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** The cards out of an overview body; a malformed one is the query's error, and hides the strip. */
const toCards = (body: unknown) => toOverview(body).cards;

export function LiveStatStrip() {
  // ponytail: a malformed overview hides the strip; Overview itself reports it.
  const { data: cards } = useIntent({ type: "overview" }, { select: toCards, refetchInterval: POLL_MS });
  return cards === undefined ? null : <StatStrip cards={cards} />;
}
