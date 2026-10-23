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

import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { describe } from "../model/failure.ts";
import {
  AUTOMATIC,
  canSubmit,
  chooseFile,
  languageChoices,
  parserChoices,
  submitIntent,
  submittedRunId,
  switchKind,
  chooseSchema,
  chooseParser,
  toSchemas,
  toParsers,
  toTenants,
  type SubmitKind,
  type SubmitForm,
} from "../model/submit.ts";
import { FileField, SelectField } from "./fields.tsx";
import { useAct, useIntent } from "./useIntent.ts";

interface Props {
  onOpenRun: (runId: string) => void;
}

// `getRandomValues`, not `randomUUID`: the latter exists only in a
// secure context, and a console reached over plain http is not one.
const newKey = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");

const KINDS = [
  { value: "ocr", label: "OCR" },
  { value: "extraction", label: "Extraction" },
];

export function SubmitArea({ onOpenRun }: Props) {
  const act = useAct();
  const tenants = useIntent({ type: "list-tenants" }, { select: toTenants });
  const parsers = useIntent({ type: "list-parsers" }, { select: toParsers });
  const schemas = useIntent({ type: "list-schemas" }, { select: toSchemas });
  const failedLoad = tenants.error ?? parsers.error ?? schemas.error;
  const loadProblem = failedLoad === null ? null : describe(failedLoad);
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

  const submit = () => {
    if (form.file === null) return;
    setProblem(null);
    setForm((f) => ({ ...f, sending: true }));
    void act(submitIntent(form), form.file).then((answer) => {
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
          onChange={(kind) => setForm((f) => switchKind(f, kind as SubmitKind, newKey()))}
        />

        <SelectField
          label="Tenant"
          placeholder="Choose a tenant"
          options={tenants.data ?? []}
          value={form.tenant}
          onChange={(tenant) => setForm((f) => ({ ...f, tenant }))}
        />

        {form.kind === "extraction" ? (
          <SelectField
            label="Schema"
            placeholder="Choose a schema"
            options={schemas.data ?? []}
            value={form.schema}
            onChange={(schema) => setForm((f) => chooseSchema(f, schema, newKey()))}
          />
        ) : (
          <>
            <SelectField
              label="Parser"
              options={parserChoices(parsers.data ?? [])}
              value={form.parser}
              onChange={(parser) =>
                setForm((f) => chooseParser(f, parser, newKey()))
              }
            />

            <SelectField
              label="Language"
              options={languageChoices(parsers.data ?? [], form.parser)}
              value={form.language}
              onChange={(language) => setForm((f) => ({ ...f, language }))}
            />
          </>
        )}

        <FileField
          label="Document"
          value={form.file}
          onChange={(file) => setForm((f) => chooseFile(f, file, newKey()))}
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
