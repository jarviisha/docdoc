/**
 * Where an operator signs in (ADR-0023 §1).
 *
 * A real `<form>`, so Enter submits it, with native inputs inside `Field`s so
 * each carries an accessible name and is operable by keyboard (FR-045a). The
 * `autoComplete` values are the standard ones: a console account is a
 * username and a password, and a password manager remembering it is the
 * operator's choice — the session itself is an `HttpOnly` cookie this page
 * never reads.
 *
 * It decides nothing. What a refusal says is `notice`, which `Shell` takes
 * from `describe(failure)`; the password is passed up once and cleared here.
 */

import { useState, type FormEvent } from "react";
import { Banner, Button, Field, Stack, Text } from "@astryxdesign/core";

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
    <form onSubmit={submit}>
      <Stack direction="vertical" gap={3}>
        <Text size="lg" weight="bold">
          docdoc operations console
        </Text>

        <div role="status" aria-live="polite">
          {notice === null ? null : <Banner status="warning" title={notice} />}
        </div>

        <Field label="Username" inputID="username">
          <input
            id="username"
            type="text"
            autoComplete="username"
            spellCheck={false}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </Field>

        <Field label="Password" inputID="password">
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <Button
          type="submit"
          label="Sign in"
          variant="primary"
          isDisabled={username.trim() === "" || password === ""}
        />
      </Stack>
    </form>
  );
}
