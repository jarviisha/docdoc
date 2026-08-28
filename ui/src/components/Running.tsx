/**
 * What a person sees while a run is in flight.
 *
 * The label comes from the view model and says one thing: how long it has been
 * running. There is no progress bar, no percentage and no estimate, because the
 * architecture knows none of them — a run happens inside one request, with no
 * queue and no stage reporting to the browser — and an invented proportion is
 * the same category of error as an invented location (FR-045, FR-046).
 *
 * Astryx ships a `ProgressBar`, and it is not used here for exactly that reason:
 * a determinate bar would have to be given a number this system does not have.
 * `Spinner` says "working" without claiming to know how much is left.
 *
 * **There is no cancel button, and its absence is a decision.** Closing the page
 * stops the waiting, not the run: the provider is already being paid, and the
 * extraction continues to completion on the server. A control labelled "cancel"
 * would be this interface's first lie (FR-047). Nor is there a client-side
 * timeout — any bound on the wait belongs to the deployment's proxy (FR-048).
 */

import { Spinner, Stack, Text } from "@astryxdesign/core";

interface Props {
  label: string | null;
}

export function Running({ label }: Props) {
  if (label === null) return null;

  return (
    <Stack direction="horizontal" gap={2} role="status" aria-live="polite">
      <Spinner />
      <Stack direction="vertical" gap={1}>
        <Text size="base">{label}</Text>
        <Text size="xsm">
          Leaving this page stops the waiting, not the run — the extraction continues and its
          cost is already incurred.
        </Text>
      </Stack>
    </Stack>
  );
}
