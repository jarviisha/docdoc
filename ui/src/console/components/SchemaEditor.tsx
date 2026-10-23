/**
 * Author a schema: a new name, or a new version of one — `POST /v1/console/schemas`.
 *
 * Nothing is ever updated: a save always creates the next version, and the
 * server assigns its number. Leaving with unsaved edits asks first.
 *
 * **It decides nothing.** The draft, its edits, the save label, the document
 * sent, the changes listed and the rows a refusal marks are
 * `console/model/schemas.ts`; what a failure says is `console/model/failure.ts`.
 */

import { useId, useRef, useState } from "react";
import { Link, useBlocker, useNavigate } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

import { describe } from "../model/failure.ts";
import {
  addField,
  CARDINALITIES,
  changesBetween,
  hasChanges,
  constraintsFor,
  constraintText,
  documentOf,
  draftFrom,
  emptyDraft,
  FIELD_TYPES,
  groupByName,
  isDirty,
  isNumberConstraint,
  moveField,
  problemRows,
  removeField,
  saveLabel,
  toSchemaDetail,
  toSchemaEntries,
  updateField,
  visibleRows,
  withConstraint,
  type Cardinality,
  type FieldDraft,
  type FieldType,
  type SchemaDraft,
} from "../model/schemas.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { SelectField, TextAreaField, TextField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { useAct, useIntent } from "./useIntent.ts";

/** Loads the base, when there is one, then hands the form its starting draft. */
export function SchemaEditor({ base }: { base?: string | undefined }) {
  const detail = useIntent(base === undefined ? null : { type: "schema-detail", identity: base }, {
    select: toSchemaDetail,
  });
  if (base === undefined) return <DraftForm start={emptyDraft()} base={null} />;
  const loaded = detail.data;
  if (loaded !== undefined && loaded !== null) {
    return <DraftForm start={draftFrom(loaded.document, loaded.prompt)} base={loaded.identity} />;
  }
  return (
    <div role="status" aria-live="polite">
      {detail.error === null && loaded === undefined ? <p className="text-sm">Loading…</p> : null}
      {detail.error === null ? null : (
        <Alert variant="destructive">
          <AlertTitle>The base schema could not be read</AlertTitle>
          <AlertDescription>{describe(detail.error)}</AlertDescription>
        </Alert>
      )}
      {loaded === null ? (
        <Alert variant="destructive">
          <AlertTitle>The deployment's answer was not a schema</AlertTitle>
        </Alert>
      ) : null}
    </div>
  );
}

type Tab = "fields" | "prompt" | "rules" | "changes";

interface Refusal {
  message: string;
  problems: { path: string; message: string }[];
}

function DraftForm({ start, base }: { start: SchemaDraft; base: string | null }) {
  const act = useAct();
  const navigate = useNavigate();
  const list = useIntent({ type: "list-schemas" }, { select: toSchemaEntries });
  const [initial] = useState(start);
  const [draft, setDraft] = useState(start);
  const [tab, setTab] = useState<Tab>("fields");
  const [sending, setSending] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  // Rows the last refusal named, by `TreeRow.key`.
  const [marked, setMarked] = useState<Set<string>>(new Set());
  // Set once a save lands, so navigating to the new version is not blocked.
  const saved = useRef(false);
  const dirty = isDirty(initial, draft);
  const blocker = useBlocker({
    shouldBlockFn: () => dirty && !saved.current,
    enableBeforeUnload: () => dirty && !saved.current,
    withResolver: true,
  });

  const save = async () => {
    setRefusal(null);
    setMarked(new Set());
    const built = documentOf(draft);
    if ("error" in built) return setRefusal({ message: built.error, problems: [] });
    setSending(true);
    const answer = await act(
      { type: "create-schema" },
      JSON.stringify({ document: built.document, prompt: draft.prompt, base }),
    );
    setSending(false);
    if (answer.ok) {
      const created = toSchemaDetail(answer.body);
      saved.current = true;
      void navigate(
        created === null ? { to: "/schemas" } : { to: "/schemas/$identity", params: { identity: created.identity } },
      );
      return;
    }
    const problems = answer.failure.kind === "reported" ? (answer.failure.problems ?? []) : [];
    setRefusal({ message: describe(answer.failure), problems });
    setMarked(problemRows(draft, problems));
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "fields", label: "Fields" },
    { id: "prompt", label: "Prompt" },
    { id: "rules", label: "Rules" },
    ...(base === null ? [] : [{ id: "changes" as const, label: "Changes" }]),
  ];
  const changes = changesBetween(initial, draft);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end gap-2">
        <Button asChild variant="ghost" size="sm">
          <Link to="/schemas">← Back</Link>
        </Button>
        <TextField
          label="Name"
          className="w-[280px]"
          value={draft.name}
          readOnly={base !== null}
          spellCheck={false}
          {...(base === null ? { onChange: (name: string) => setDraft({ ...draft, name }) } : {})}
        />
        {base === null ? null : (
          <span className="pb-1 text-xs text-slate-500">
            from <code className="font-mono">{base}</code>
          </span>
        )}
        <span className="flex-1" />
        <Button disabled={sending} onClick={() => void save()}>
          {sending ? "Saving…" : saveLabel(groupByName(list.data ?? []), draft.name)}
        </Button>
      </div>

      <div role="status" aria-live="polite" className="empty:-mb-3">
        {refusal === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The schema was not saved</AlertTitle>
            <AlertDescription>
              <p>{refusal.message}</p>
              {refusal.problems.length === 0 ? null : (
                <ul className="list-disc pl-4">
                  {refusal.problems.map((p) => (
                    <li key={`${p.path}:${p.message}`}>
                      <code className="font-mono text-xs">{p.path}</code>: {p.message}
                    </li>
                  ))}
                </ul>
              )}
            </AlertDescription>
          </Alert>
        )}
      </div>

      <div role="tablist" aria-label="Schema draft" className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "-mb-px border-b-2 border-transparent px-3 py-1 text-slate-600 outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              tab === t.id && "border-primary font-medium text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" aria-label={tabs.find((t) => t.id === tab)?.label} className="flex flex-col gap-3">
        {tab === "fields" ? <FieldTable draft={draft} onChange={setDraft} marked={marked} /> : null}
        {tab === "prompt" ? (
          <TextAreaField label="Prompt" value={draft.prompt} onChange={(prompt) => setDraft({ ...draft, prompt })} />
        ) : null}
        {tab === "rules" ? (
          <TextAreaField
            label="Rules (a JSON array)"
            value={draft.rulesText}
            onChange={(rulesText) => setDraft({ ...draft, rulesText })}
          />
        ) : null}
        {tab === "changes" ? (
          hasChanges(changes) ? (
            <div className="flex flex-col gap-2 text-sm">
              <PathList heading="Added" paths={changes.added} />
              <PathList heading="Removed" paths={changes.removed} />
              <PathList heading="Changed" paths={changes.changed} />
              {changes.orderChanged ? <p>Field order changed.</p> : null}
              {changes.promptChanged ? <p>The prompt changed.</p> : null}
              {changes.rulesChanged ? <p>The rules changed.</p> : null}
            </div>
          ) : (
            <p className="text-sm text-slate-600">No field or prompt changes from {base}.</p>
          )
        ) : null}
      </div>

      <ConfirmDialog
        open={blocker.status === "blocked"}
        onOpenChange={(open) => {
          if (!open) blocker.reset?.();
        }}
        title="Leave without saving?"
        description="The edits to this draft are lost; nothing has been saved."
        confirmLabel="Leave"
        cancelLabel="Keep editing"
        onConfirm={async () => blocker.proceed?.()}
      />
    </div>
  );
}

function PathList({ heading, paths }: { heading: string; paths: string[] }) {
  if (paths.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <SectionHeading>{heading}</SectionHeading>
      <ul className="flex flex-col gap-0.5">
        {paths.map((path) => (
          <li key={path}>
            <code className="font-mono text-xs">{path === "" ? "(unnamed)" : path}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The field tree: a row per visible field, groups expand, a click opens the row's editor beneath it. */
function FieldTable({
  draft,
  onChange,
  marked,
}: {
  draft: SchemaDraft;
  onChange: (draft: SchemaDraft) => void;
  marked: Set<string>;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (key: string) => {
    const next = new Set(expanded);
    if (!next.delete(key)) next.add(key);
    setExpanded(next);
  };
  // Indices shift on a move or a remove, so whatever was open is closed rather than misplaced.
  const reshape = (next: SchemaDraft) => {
    setOpen(null);
    onChange(next);
  };

  return (
    <>
      <Card className="py-0">
        <Table aria-label="Fields">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead style={{ width: "140px" }}>Type</TableHead>
              <TableHead style={{ width: "90px" }}>Required</TableHead>
              <TableHead>Constraints</TableHead>
              <TableHead style={{ width: "260px" }} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {visibleRows(draft.fields, expanded).flatMap(({ field, path, key, depth, expandable }) => {
              const label = field.name === "" ? "(unnamed)" : field.name;
              const rows = [
                <TableRow key={key} className={cn("even:bg-slate-50/60", marked.has(key) && "bg-red-50 even:bg-red-50")}>
                  <TableCell>
                    <span className="flex items-center gap-1" style={{ paddingLeft: `${depth * 16}px` }}>
                      {expandable ? (
                        <button
                          type="button"
                          aria-label={`${expanded.has(key) ? "Collapse" : "Expand"} ${label}`}
                          aria-expanded={expanded.has(key)}
                          onClick={() => toggle(key)}
                          className="w-4 text-slate-500"
                        >
                          {expanded.has(key) ? "▾" : "▸"}
                        </button>
                      ) : (
                        <span className="w-4" />
                      )}
                      <button
                        type="button"
                        aria-expanded={open === key}
                        onClick={() => setOpen(open === key ? null : key)}
                        className="font-mono text-xs text-primary underline-offset-2 hover:underline"
                      >
                        {label}
                      </button>
                      {marked.has(key) ? <Badge variant="error">problem</Badge> : null}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs">{field.type ?? field.cardinality}</TableCell>
                  <TableCell className="text-xs">{field.required ? "yes" : "—"}</TableCell>
                  <TableCell>
                    <code className="font-mono text-xs text-slate-600">
                      {Object.keys(field.constraints).join(", ")}
                    </code>
                  </TableCell>
                  <TableCell>
                    <span className="flex justify-end gap-1">
                      {expandable ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setExpanded(new Set(expanded).add(key));
                            onChange(addField(draft, path));
                          }}
                        >
                          Add child
                        </Button>
                      ) : null}
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Move ${label} up`}
                        onClick={() => reshape(moveField(draft, path, -1))}
                      >
                        ↑
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Move ${label} down`}
                        onClick={() => reshape(moveField(draft, path, 1))}
                      >
                        ↓
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Remove ${label}`}
                        onClick={() => reshape(removeField(draft, path))}
                      >
                        Remove
                      </Button>
                    </span>
                  </TableCell>
                </TableRow>,
              ];
              if (open === key) {
                rows.push(
                  <TableRow key={`${key}:edit`} className="bg-white hover:bg-white">
                    <TableCell colSpan={5}>
                      <FieldEditor
                        key={key}
                        field={field}
                        onChange={(patch) => onChange(updateField(draft, path, patch))}
                      />
                    </TableCell>
                  </TableRow>,
                );
              }
              return rows;
            })}
          </TableBody>
        </Table>
      </Card>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => onChange(addField(draft, []))}>
          Add field
        </Button>
      </div>
    </>
  );
}

function FieldEditor({ field, onChange }: { field: FieldDraft; onChange: (patch: Partial<FieldDraft>) => void }) {
  const requiredId = useId();
  return (
    <div className="flex max-w-[720px] flex-col gap-3 py-2 whitespace-normal">
      <div className="flex items-end gap-3">
        <TextField
          label="Name"
          className="w-[200px]"
          spellCheck={false}
          value={field.name}
          onChange={(name) => onChange({ name })}
        />
        <SelectField
          label="Cardinality"
          className="w-[160px]"
          options={[...CARDINALITIES]}
          value={field.cardinality}
          onChange={(cardinality) => onChange({ cardinality: cardinality as Cardinality })}
        />
        {field.cardinality === "scalar" ? (
          <SelectField
            label="Type"
            className="w-[140px]"
            options={[...FIELD_TYPES]}
            value={field.type ?? ""}
            onChange={(type) => onChange({ type: type as FieldType })}
          />
        ) : null}
        <span className="flex h-[26px] items-center gap-1">
          <input
            id={requiredId}
            type="checkbox"
            checked={field.required}
            onChange={(event) => onChange({ required: event.target.checked })}
          />
          <Label htmlFor={requiredId} className="text-xs font-normal text-slate-600">
            Required
          </Label>
        </span>
      </div>
      <TextField label="Description" value={field.description} onChange={(description) => onChange({ description })} />
      {constraintsFor(field).length === 0 ? null : (
        <div className="flex flex-wrap items-end gap-3">
          {constraintsFor(field).map((key) => (
            <ConstraintInput key={`${field.cardinality}:${field.type}:${key}`} field={field} name={key} onChange={onChange} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Holds its own text: `1,` must stay `1,` while typed, though the constraint it sets is `[1]`. */
function ConstraintInput({
  field,
  name,
  onChange,
}: {
  field: FieldDraft;
  name: string;
  onChange: (patch: Partial<FieldDraft>) => void;
}) {
  const [text, setText] = useState(constraintText(field.constraints[name]));
  return (
    <TextField
      label={name === "enum" ? "enum (comma list)" : name}
      className={name === "enum" || name === "pattern" ? "w-[280px]" : "w-[120px]"}
      type={isNumberConstraint(field, name) ? "number" : "text"}
      spellCheck={false}
      value={text}
      onChange={(next) => {
        setText(next);
        onChange({ constraints: withConstraint(field, name, next) });
      }}
    />
  );
}
