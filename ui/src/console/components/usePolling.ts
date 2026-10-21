/**
 * The timer mechanics of a self-refreshing area; every decision is in
 * `console/model/polling.ts`.
 *
 * The next poll is armed only after the last one settles, so a request
 * slower than `POLL_MS` is never overlapped; the tab becoming visible again
 * refreshes at once, unless a request is still in flight.
 */

import { useEffect, useRef } from "react";

import { nextPoll, onVisible, startsNow } from "../model/polling.ts";

export function usePolling(refresh: () => Promise<void>, enabled: boolean) {
  // The latest `refresh`, read when a poll fires: re-arming the timer on every
  // render that changes it would also reset the in-flight flag below.
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  });

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wasHidden = document.hidden;

    const schedule = () => {
      clearTimeout(timer);
      const delay = nextPoll(document.hidden);
      if (delay !== null) timer = setTimeout(run, delay);
    };
    const run = () => {
      if (!startsNow(inFlight)) return;
      inFlight = true;
      void latest.current().finally(() => {
        inFlight = false;
        if (active) schedule();
      });
    };
    const visibilityChanged = () => {
      const hidden = document.hidden;
      const now = onVisible(wasHidden, hidden);
      wasHidden = hidden;
      if (now && startsNow(inFlight)) run();
      else if (!inFlight) schedule();
    };

    document.addEventListener("visibilitychange", visibilityChanged);
    schedule();
    return () => {
      active = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [enabled]);
}
