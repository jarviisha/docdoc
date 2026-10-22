/**
 * The four counts as one bordered strip: on Overview, and above the run list.
 *
 * `LiveStatStrip` reads the overview itself for the run list. It is a glance,
 * not a report: until an answer arrives, or when one fails, it shows nothing,
 * and the list below it says what went wrong with the request that matters.
 */

import { useCallback, useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import type { Intent } from "../model/client.ts";
import type { Failure } from "../model/failure.ts";
import { cardTone, toOverview, type OverviewCards } from "../model/overview.ts";
import { usePolling } from "./usePolling.ts";

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

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

export function LiveStatStrip({ request }: { request: Request }) {
  const [cards, setCards] = useState<OverviewCards | null>(null);

  const refresh = useCallback(async () => {
    const answer = await request({ type: "overview" });
    if (!answer.ok) return;
    try {
      setCards(toOverview(answer.body).cards);
    } catch {
      // ponytail: a malformed overview hides the strip; Overview itself reports it.
    }
  }, [request]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  usePolling(refresh, true);

  return cards === null ? null : <StatStrip cards={cards} />;
}
