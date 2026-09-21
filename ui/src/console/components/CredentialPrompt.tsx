/**
 * Where the key is entered, and the only place it is ever typed.
 *
 * A native `<input type="password">` inside a `Field`, so it carries an
 * accessible name and is operable by keyboard with nothing added (FR-045a).
 * `autoComplete="off"` because a browser password manager storing this would put
 * the credential at rest by a route FR-007 cannot see — `check-no-persistence.mjs`
 * scans this repository's source, not Chrome's.
 *
 * The value goes to the session model and nowhere else. It is not logged, not
 * placed in a URL, and not held here after submission.
 */

import { useState } from "react";
import { Button, Field, Stack, Text } from "@astryxdesign/core";

interface Props {
  onCredential: (pasted: string) => void;
  onContinueWithout: () => void;
}

/**
 * The heading and the refusal notice are the shell's, not this component's. They
 * were both here until the console grew an app shell, which gave each one a slot
 * that outlives the gate — and a refusal announced by a component that unmounts
 * the moment it succeeds is announced to nobody.
 */
export function CredentialPrompt({ onCredential, onContinueWithout }: Props) {
  const [pasted, setPasted] = useState("");

  return (
    <Stack direction="vertical" gap={3}>
      <Text size="sm">
        Paste the API key this deployment issued you. It is held for this page only — a reload, and
        fifteen minutes of inactivity, both require it again.
      </Text>

      <Field label="API key" inputID="credential">
        <input
          id="credential"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={pasted}
          onChange={(event) => setPasted(event.target.value)}
        />
      </Field>

      <Stack direction="horizontal" gap={2}>
        <Button
          label="Continue"
          variant="primary"
          isDisabled={pasted.trim() === ""}
          onClick={() => onCredential(pasted)}
        />
        {/* An unauthenticated deployment is the Milestone 9 default and a
            supported configuration (FR-011). The console finds out by trying,
            because a route reporting the mode would be a second new read and an
            oracle describing the deployment's posture. */}
        <Button
          label="This deployment has no authentication"
          variant="secondary"
          onClick={onContinueWithout}
        />
      </Stack>
    </Stack>
  );
}
