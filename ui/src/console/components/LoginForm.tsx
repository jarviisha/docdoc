/**
 * Where an operator signs in (ADR-0023 §1).
 *
 * A real `<form>`, so Enter submits it, with labelled `TextField`s so each
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
import { Alert, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { TextField } from "./fields.tsx";

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
    <Card className="w-full max-w-[380px]">
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <p className="text-lg font-bold">docdoc operations console</p>

          <div role="status" aria-live="polite">
            {notice === null ? null : (
              <Alert variant="warning">
                <AlertTitle>{notice}</AlertTitle>
              </Alert>
            )}
          </div>

          <TextField
            label="Username"
            autoComplete="username"
            spellCheck={false}
            value={username}
            onChange={setUsername}
          />

          <TextField
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
          />

          <Button type="submit" disabled={username.trim() === "" || password === ""}>
            Sign in
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
