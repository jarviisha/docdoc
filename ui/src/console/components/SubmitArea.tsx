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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

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
  switchKind,
  toSchemas,
  toParsers,
  toTenants,
  type ParserOption,
  type SubmitKind,
  type SubmitForm,
} from "../model/submit.ts";
import { FileField, SelectField } from "./fields.tsx";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onOpenRun: (runId: string) => void;
}

const KINDS = [
  { value: "ocr", label: "OCR" },
  { value: "extraction", label: "Extraction" },
];

export function SubmitArea({ request, onOpenRun }: Props) {
  const [tenants, setTenants] = useState<string[]>([]);
  const [parsers, setParsers] = useState<ParserOption[]>([]);
  const [schemas, setSchemas] = useState<string[]>([]);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [form, setForm] = useState<SubmitForm>({
    kind: "ocr",
    schema: "",
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
    void request({ type: "list-schemas" }).then((answer) => {
      if (!current) return;
      if (answer.ok) setSchemas(toSchemas(answer.body));
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
    <Card className="w-full max-w-[520px]">
      <CardContent className="flex flex-col gap-4">
        <div role="status" aria-live="polite">
          {loadProblem === null ? null : (
            <Alert variant="destructive">
              <AlertTitle>
                Tenants, parsers or schemas could not be loaded
              </AlertTitle>
              <AlertDescription>{loadProblem}</AlertDescription>
            </Alert>
          )}
          {problem === null ? null : (
            <Alert variant="destructive">
              <AlertTitle>The document was not submitted</AlertTitle>
              <AlertDescription>{problem}</AlertDescription>
            </Alert>
          )}
        </div>

        <SelectField
          label="Submit as"
          options={KINDS}
          value={form.kind}
          onChange={(kind) => setForm((f) => switchKind(f, kind as SubmitKind))}
        />

        <SelectField
          label="Tenant"
          placeholder="Choose a tenant"
          options={tenants}
          value={form.tenant}
          onChange={(tenant) => setForm((f) => ({ ...f, tenant }))}
        />

        {form.kind === "extraction" ? (
          <SelectField
            label="Schema"
            placeholder="Choose a schema"
            options={schemas}
            value={form.schema}
            onChange={(schema) => setForm((f) => ({ ...f, schema }))}
          />
        ) : (
          <>
            <SelectField
              label="Parser"
              options={parserChoices(parsers)}
              value={form.parser}
              onChange={(parser) =>
                setForm((f) => ({ ...f, parser, language: "" }))
              }
            />

            <SelectField
              label="Language"
              options={languageChoices(parsers, form.parser)}
              value={form.language}
              onChange={(language) => setForm((f) => ({ ...f, language }))}
            />
          </>
        )}

        <FileField
          label="Document"
          value={form.file}
          // `getRandomValues`, not `randomUUID`: the latter exists only in a
          // secure context, and a console reached over plain http is not one.
          onChange={(file) => {
            const key = Array.from(
              crypto.getRandomValues(new Uint8Array(16)),
              (b) => b.toString(16).padStart(2, "0"),
            ).join("");
            setForm((f) => chooseFile(f, file, key));
          }}
        />

        <div className="flex gap-2">
          <Button disabled={!canSubmit(form)} onClick={submit}>
            {form.sending ? "Submitting…" : "Submit"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
