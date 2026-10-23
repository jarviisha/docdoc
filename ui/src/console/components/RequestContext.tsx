/**
 * The shell's `request` and `requestImage`, handed to every route.
 *
 * Routes are rendered by the router, not by `Shell`, so the two functions can
 * no longer be passed down as props from where they are made. They still are
 * made in exactly one place: a session ending is acted on there, whichever
 * area noticed it (FR-010).
 */

import { createContext, useContext, useMemo, type ReactNode } from "react";

import type { Intent } from "../model/client.ts";
import type { Failure } from "../model/failure.ts";
import type { ImageRequest } from "./ResultViewer.tsx";

export type { ImageRequest };

/** One request, classified; the session ending is already handled. */
export type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Requests {
  request: Request;
  requestImage: ImageRequest;
}

const RequestContext = createContext<Requests | null>(null);

export function RequestProvider({ request, requestImage, children }: Requests & { children: ReactNode }) {
  const value = useMemo(() => ({ request, requestImage }), [request, requestImage]);
  return <RequestContext value={value}>{children}</RequestContext>;
}

export function useRequest(): Requests {
  const requests = useContext(RequestContext);
  if (requests === null) throw new Error("useRequest outside RequestProvider: only a signed-in shell renders routes");
  return requests;
}
