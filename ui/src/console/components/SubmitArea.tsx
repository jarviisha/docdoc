/**
 * Submit one document for OCR on a tenant's behalf —
 * `POST /v1/console/tenants/{id}/ocr`, the same path a tenant's own key takes.
 *
 * **It decides nothing.** Which parsers and languages are offered, whether
 * Submit may be pressed, what the request is and which run it answered with
 * are `console/model/submit.ts`; what a failure says is
 * `console/model/failure.ts`. A tenant or parser list that fails to load says
 * so and leaves the form usable with whatever did load.
 */

import { useEffect, useState } from "react";
import { Banner, Button, Card, FileInput, HStack, Selector, Stack, Text } from "@astryxdesign/core";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import {
  AUTOMATIC,
  canSubmit,
  chooseFile,
  languageChoices,
  parserChoices,
  submitIntent,
  submittedRunId,
  toParsers,
  toTenants,
  type ParserOption,
  type SubmitForm,
} from "../model/submit.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onOpenRun: (runId: string) => void;
}

export function SubmitArea({ request, onOpenRun }: Props) {
  const [tenants, setTenants] = useState<string[]>([]);
  const [parsers, setParsers] = useState<ParserOption[]>([]);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [form, setForm] = useState<SubmitForm>({
    tenant: "",
    parser: AUTOMATIC,
    language: "",
    file: null,
    key: "",
    sending: false,
  });

  useEffect(() => {
    let current = true;
    void request({ type: "list-tenants" }).then((answer) => {
      if (!current) return;
      if (answer.ok) setTenants(toTenants(answer.body));
      else setLoadProblem(describe(answer.failure));
    });
    void request({ type: "list-parsers" }).then((answer) => {
      if (!current) return;
      if (answer.ok) setParsers(toParsers(answer.body));
      else setLoadProblem(describe(answer.failure));
    });
    return () => {
      current = false;
    };
  }, [request]);

  const submit = () => {
    if (form.file === null) return;
    setProblem(null);
    setForm((f) => ({ ...f, sending: true }));
    void request(submitIntent(form), form.file).then((answer) => {
      setForm((f) => ({ ...f, sending: false }));
      if (!answer.ok) return setProblem(describe(answer.failure));
      const runId = submittedRunId(answer.body);
      if (runId !== null) onOpenRun(runId);
    });
  };

  return (
    <Card width="100%" maxWidth={520} padding={6}>
      <Stack direction="vertical" gap={4}>
        <Text size="lg" weight="bold">
          Submit OCR
        </Text>

        <div role="status" aria-live="polite">
          {loadProblem === null ? null : (
            <Banner status="error" title="Tenants or parsers could not be loaded" description={loadProblem} />
          )}
          {problem === null ? null : (
            <Banner status="error" title="The document was not submitted" description={problem} />
          )}
        </div>

        <Selector
          label="Tenant"
          placeholder="Choose a tenant"
          options={tenants}
          value={form.tenant}
          onChange={(tenant) => setForm((f) => ({ ...f, tenant }))}
        />

        <Selector
          label="Parser"
          options={parserChoices(parsers)}
          value={form.parser}
          onChange={(parser) => setForm((f) => ({ ...f, parser, language: "" }))}
        />

        <Selector
          label="Language"
          options={languageChoices(parsers, form.parser)}
          value={form.language}
          onChange={(language) => setForm((f) => ({ ...f, language }))}
        />

        <FileInput
          label="Document"
          value={form.file}
          // Single-file (no `isMultiple`), so the value is a `File` or `null`.
          // `getRandomValues`, not `randomUUID`: the latter exists only in a
          // secure context, and a console reached over plain http is not one.
          onChange={(file) => {
            const key = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
              b.toString(16).padStart(2, "0"),
            ).join("");
            setForm((f) => chooseFile(f, file as File | null, key));
          }}
        />

        <HStack gap={2}>
          <Button
            label="Submit"
            variant="primary"
            isDisabled={!canSubmit(form)}
            isLoading={form.sending}
            onClick={submit}
          />
        </HStack>
      </Stack>
    </Card>
  );
}
