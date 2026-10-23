/**
 * Reads and writes through the shell's `request`, cached by TanStack Query.
 *
 * `useIntent` is one read: keyed by `keyOf(intent)`, so two areas asking the
 * same thing share one answer. `useAct` is one write: on success it marks
 * stale whatever `invalidatedBy(intent)` names, and every screen showing one
 * of those reads fetches it again. Which keys and which writes is decided in
 * `model/queries.ts`; this is only the wiring.
 */

import { useCallback } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Intent } from "../model/client.ts";
import type { Failure } from "../model/failure.ts";
import { invalidatedBy, keyOf } from "../model/queries.ts";
import { useRequest, type Request } from "./RequestContext.tsx";

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: Failure;
  }
}

export type Answer = Awaited<ReturnType<Request>>;

const identity = (body: unknown) => body;

interface IntentOptions<T> {
  /** As a function, also handed the last failure: a run that is gone (`410`) has nothing left to poll for. */
  refetchInterval?: number | false | ((data: T | undefined, error: Failure | null) => number | false);
  /** Shape the body; the default leaves it `unknown`. */
  select?: (body: unknown) => T;
  /** Keep showing the last answer while a different intent loads, so rows never blank. */
  keepPrevious?: boolean;
}

/** One read, or none while `intent` is `null`. A refused request is the query's error. */
export function useIntent<T = unknown>(intent: Intent | null, options: IntentOptions<T> = {}) {
  const { request } = useRequest();
  // A stable default: a new `select` each render would re-run it each render.
  const { refetchInterval, select = identity as (body: unknown) => T, keepPrevious = false } = options;
  return useQuery<unknown, Failure, T>({
    queryKey: intent === null ? ["none"] : keyOf(intent),
    enabled: intent !== null,
    queryFn: async () => {
      const answer = await request(intent!);
      if (!answer.ok) throw answer.failure;
      return answer.body;
    },
    select,
    ...(keepPrevious ? { placeholderData: keepPreviousData } : {}),
    ...(refetchInterval === undefined
      ? {}
      : {
          refetchInterval:
            typeof refetchInterval === "function"
              ? (query) =>
                  refetchInterval(
                    query.state.data === undefined ? undefined : select(query.state.data),
                    query.state.error,
                  )
              : refetchInterval,
        }),
  });
}

/** One write. On success, the reads it changed are fetched again wherever they are shown. */
export function useAct(): (intent: Intent, body?: BodyInit) => Promise<Answer> {
  const { request } = useRequest();
  const queryClient = useQueryClient();
  return useCallback(
    async (intent: Intent, body?: BodyInit) => {
      const answer = await request(intent, body);
      if (answer.ok) {
        const stale = invalidatedBy(intent);
        if (stale === "all") void queryClient.invalidateQueries();
        else for (const queryKey of stale) void queryClient.invalidateQueries({ queryKey });
      }
      return answer;
    },
    [request, queryClient],
  );
}
