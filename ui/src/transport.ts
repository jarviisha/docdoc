/**
 * The only place this application touches the network.
 *
 * Neither model nor component. The model constructs a `RequestPlan` and the URL
 * it addresses — both pure, both tested — and this executes one. It contains no
 * decision: it does not choose a path, a method, a query or a body, and it must
 * not start.
 *
 * It exists because a single call site is what makes the request surface
 * checkable: the set of requests this application can construct is the set
 * `console/model/client.ts` enumerates, and that is only true while nothing else
 * calls `fetch`. `check-model-boundary.mjs` fails if a second call site appears
 * under `src/console/components/`.
 *
 * `RequestPlan` and `toUrl` live here rather than in a model file because the
 * viewer was removed (ADR-0020) and they were the only part of its
 * `src/model/client.ts` the console ever used. A module holding two declarations
 * for one importer is a module that exists to be deleted.
 */

export interface RequestPlan {
  method: "GET" | "POST" | "DELETE";
  path: string;
  query: Record<string, string>;
  hasBody: boolean;
}

/**
 * The URL a plan addresses.
 *
 * Pure, and separate from `send`, because assembling a URL is a decision about
 * where a request goes and `send` decides nothing. Tested through
 * `console/model/client.ts`, which is where every plan is built.
 */
export function toUrl(plan: RequestPlan): string {
  const query = new URLSearchParams(plan.query).toString();
  return query === "" ? plan.path : `${plan.path}?${query}`;
}

export interface Response {
  ok: boolean;
  /** The status, because `401` and `404` mean different things to a console. */
  status: number;
  body: unknown;
}

/**
 * A request that never produced a readable answer.
 *
 * Thrown so the caller can tell "the deployment reported a failure" from "we
 * never heard a usable answer" — two different things that reached the user as
 * one until T102. What the distinction *means* is `failure.ts`'s to say; this
 * only reports which happened, which is mechanics rather than a decision.
 */
export class TransportError extends Error {}

/**
 * What a caller supplies beyond the plan.
 *
 * There is no credential here (ADR-0023 §1). The console authenticates with
 * `docdoc_console`, an `HttpOnly` cookie the browser attaches to every
 * same-origin request on its own — this file never reads it and never could
 * (`check-no-persistence.mjs` forbids `document.cookie` under `src/`
 * regardless). `body` is the only thing a caller still supplies.
 */
export interface Options {
  body?: BodyInit;
}

/**
 * The header every non-`GET`/`HEAD` console request must carry (ADR-0023 §1).
 *
 * `SameSite=Strict` already stops a cross-site browser from attaching the
 * session cookie; this closes the remaining gap — a plain cross-site form
 * post cannot set a header — so a write cannot be forged from another site by
 * either mechanism failing alone. Sent on every request rather than only
 * those that write: a `GET` carrying it is harmless, and the alternative is a
 * second code path to keep in step with `plan.method`.
 */
const CONSOLE_HEADER = "X-Docdoc-Console";

export async function send(plan: RequestPlan, options: Options = {}): Promise<Response> {
  const { body } = options;
  const response = await fetch(toUrl(plan), {
    method: plan.method,
    headers: {
      [CONSOLE_HEADER]: "1",
      // Only a `string` body is this console's own JSON. A `Blob` or `File`
      // body is an upload — `submit-ocr`'s raw file bytes — and setting no
      // `Content-Type` lets the browser supply the file's own (or none) and
      // the server's sniffing take it from there, rather than this layer
      // mislabelling a file as `application/json`.
      ...(plan.hasBody && typeof body === "string" ? { "Content-Type": "application/json" } : {}),
    },
    ...(plan.hasBody && body !== undefined ? { body } : {}),
  });

  // `401` is not an error to this layer — it is an answer, and a well-formed
  // one. The console's session model is what decides that a session was
  // refused; reporting it here would put that decision where no test reaches it.
  //
  // `204` has no body to read at all — `sign-out` and `revoke-credential`
  // both answer this way — and calling `.json()` on one throws the same
  // `SyntaxError` a truncated proxy response would, for a request that
  // completed exactly as asked.
  if (response.status === 204) {
    return { ok: response.ok, status: response.status, body: null };
  }
  try {
    return { ok: response.ok, status: response.status, body: await response.json() };
  } catch {
    // A proxy timing out mid-flight answers with HTML, not JSON. Letting the
    // `SyntaxError` escape presented a JSON parse failure to the user as the
    // *run's* cause — the deployment blamed for a body it never sent.
    throw new TransportError(
      `the deployment answered ${response.status} with a body this page could not read`,
    );
  }
}
