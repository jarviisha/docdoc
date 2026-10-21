/**
 * Where an operator signs in (ADR-0023 §1).
 *
 * A real `<form>`, so Enter submits it, with labelled `TextInput`s so each
 * carries an accessible name and is operable by keyboard (FR-045a). The
 * `autoComplete` values are the standard ones: a console account is a
 * username and a password, and a password manager remembering it is the
 * operator's choice — the session itself is an `HttpOnly` cookie this page
 * never reads.
 *
 * It decides nothing. What a refusal says is `notice`, which `Shell` takes
 * from `describe(failure)`; the password is passed up once and cleared here.
 */

import { useState, type FormEvent } from "react";
import { Banner, Button, Card, Stack, Text, TextInput } from "@astryxdesign/core";

interface Props {
  /** The refusal, the unavailable sentence, or the session-ended notice. */
  notice: string | null;
  onSubmit: (username: string, password: string) => void;
}

export function LoginForm({ notice, onSubmit }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (username.trim() === "" || password === "") return;
    onSubmit(username, password);
    setPassword("");
  };

  return (
    <Card width="100%" maxWidth={400} padding={6}>
      <form onSubmit={submit}>
      <Stack direction="vertical" gap={4}>
        <Text size="lg" weight="bold">
          docdoc operations console
        </Text>

        <div role="status" aria-live="polite">
          {notice === null ? null : <Banner status="warning" title={notice} />}
        </div>

        {/* `autoComplete` and `spellCheck` are not in TextInput's typed props,
            but its rest props land on the `<input>`; spread to get past the type. */}
        <TextInput
          label="Username"
          {...{ autoComplete: "username", spellCheck: false }}
          value={username}
          onChange={setUsername}
        />

        <TextInput
          label="Password"
          type="password"
          {...{ autoComplete: "current-password" }}
          value={password}
          onChange={setPassword}
        />

        <Button
          type="submit"
          label="Sign in"
          variant="primary"
          isDisabled={username.trim() === "" || password === ""}
        />
      </Stack>
      </form>
    </Card>
  );
}
