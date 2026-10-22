/**
 * What happened when the deployment tried to tell a client (FR-034).
 *
 * **T074, appended by convergence.** T061 claimed this file existed and it did
 * not: `model/delivery.ts` parsed every attempt and the run detail rendered one
 * summary sentence over the top of them. The sentence answers *was anyone told?*
 * and the operator's actual question is *what did they say?* — which is the
 * question the attempts answer and nothing was showing.
 *
 * The summary stays as the heading. Two different questions, two answers, and
 * the list below is the one somebody opened this screen for.
 *
 * **No signing secret appears here** (FR-035), because none reaches the model:
 * the route does not return one and `Attempt` has nowhere to put it.
 *
 * **No re-delivery, no cancellation, no destination editing** (FR-036).
 * Delivery is at-least-once and comes to rest by itself (ADR-0018); a manual
 * re-send is a second delivery path with its own ordering questions, and it
 * belongs to a milestone that answers them rather than to the screen that
 * noticed it was missing. `NOT_OFFERED` in the model is the record of that.
 */

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

import { summarise, type Attempt, type Delivery } from "../model/delivery.ts";
import { DataTable } from "./DataTable.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";

/** A receiver's answer, coloured — with the number always beside the colour. */
function tone(attempt: Attempt): "success" | "error" | "warning" {
  if (attempt.status === null) return "warning"; // never got an answer at all
  return attempt.status >= 200 && attempt.status < 300 ? "success" : "error";
}

export function DeliveryRecord({ delivery }: { delivery: Delivery }) {
  const attempts = delivery.state === "none" ? [] : delivery.attempts;

  return (
    <div className="flex flex-col gap-2">
      <SectionHeading>Delivery</SectionHeading>

      {/* The sentence first: it is the answer when there are no attempts to
          show, and an empty table in that case reads as "something went wrong"
          rather than as "nothing was owed". */}
      <p className="text-sm">{summarise(delivery)}</p>

      {attempts.length === 0 ? null : (
        <Card className="py-0">
          <DataTable
            rows={attempts}
            // Attempts carry no id of their own; their order is the record's.
            rowKey={(_, index) => String(index)}
            label="Delivery attempts, and what the receiver answered"
            columns={[
              {
                key: "attempted_at",
                header: "Attempted",
                width: "200px",
                cell: (attempt) => <Time iso={attempt.attempted_at} />,
              },
              {
                key: "status",
                header: "Answered",
                width: "120px",
                // `no answer` rather than a blank cell: a receiver that never
                // replied and one that replied `500` are different failures,
                // and the second is the one worth retrying.
                cell: (attempt) => (
                  <Badge variant={tone(attempt)}>
                    {attempt.status === null ? "no answer" : String(attempt.status)}
                  </Badge>
                ),
              },
              {
                key: "outcome",
                header: "Outcome",
                width: "auto",
                cell: (attempt) => <span className="text-sm">{attempt.outcome}</span>,
              },
            ]}
          />
        </Card>
      )}
    </div>
  );
}
