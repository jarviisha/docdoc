/**
 * One schema, read-only — `GET /v1/console/schemas/{identity}`. Rows are
 * immutable, so the only thing to do here is start a new version from it.
 *
 * **It decides nothing.** Reading the body and the draft's tree are
 * `console/model/schemas.ts`; what a failure says is `console/model/failure.ts`.
 */

import { Link } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { describe } from "../model/failure.ts";
import { draftFrom, sourceLabel, toSchemaDetail, type FieldDraft } from "../model/schemas.ts";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";
import { useIntent } from "./useIntent.ts";

function FieldList({ fields }: { fields: FieldDraft[] }) {
  return (
    <ul className="flex flex-col gap-1 border-l pl-3">
      {fields.map((field) => (
        <li key={field.name} className="flex flex-col gap-1">
          <span className="flex flex-wrap items-baseline gap-2">
            <code className="font-mono text-xs">{field.name}</code>
            <span className="text-xs text-slate-500">
              {field.type ?? field.cardinality}
              {field.required ? " · required" : ""}
            </span>
            {Object.keys(field.constraints).length > 0 ? (
              <code className="font-mono text-xs text-slate-500">{JSON.stringify(field.constraints)}</code>
            ) : null}
            {field.description === "" ? null : <span className="text-xs text-slate-600">{field.description}</span>}
          </span>
          {field.fields.length > 0 ? <FieldList fields={field.fields} /> : null}
        </li>
      ))}
    </ul>
  );
}

export function SchemaView({ identity }: { identity: string }) {
  const { data: detail, error } = useIntent({ type: "schema-detail", identity }, { select: toSchemaDetail });
  const problem = error === null ? null : describe(error);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="sm">
          <Link to="/schemas">← Back</Link>
        </Button>
      </div>

      <div role="status" aria-live="polite" className="empty:-mb-3">
        {detail === undefined && problem === null ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>This schema could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {detail === null ? (
          <Alert variant="destructive">
            <AlertTitle>The deployment's answer was not a schema</AlertTitle>
          </Alert>
        ) : null}
      </div>

      {detail === undefined || detail === null ? null : (
        <>
          <code className="font-mono text-sm">{detail.identity}</code>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Source</dt>
            <dd>{sourceLabel(detail)}</dd>
            <dt className="text-muted-foreground">Created</dt>
            <dd>
              <Time iso={detail.createdAt} />
            </dd>
            <dt className="text-muted-foreground">Created by</dt>
            <dd>{detail.createdBy ?? "—"}</dd>
            <dt className="text-muted-foreground">Based on</dt>
            <dd>
              <code className="font-mono text-xs">{detail.base ?? "—"}</code>
            </dd>
          </dl>
          <div className="flex gap-2">
            <Button asChild variant="outline">
              <Link to="/schemas/new" search={{ base: detail.identity }}>
                New version
              </Link>
            </Button>
          </div>

          <SectionHeading>Fields</SectionHeading>
          <FieldList fields={draftFrom(detail.document, detail.prompt).fields} />

          <SectionHeading>Prompt</SectionHeading>
          <pre className="max-h-96 overflow-auto rounded-sm border bg-slate-50 p-3 font-mono text-xs whitespace-pre-wrap">
            {detail.prompt}
          </pre>
        </>
      )}
    </div>
  );
}
