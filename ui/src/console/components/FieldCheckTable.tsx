/**
 * An extraction run's fields, each marked Correct or Wrong by the operator.
 *
 * **It decides nothing.** What a row shows, which marks are saved, and
 * whether Save is armed are `console/model/fields.ts`'s. Hovering a row
 * names its field to the parent, which draws its box on the page.
 */

import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

import {
  canSave,
  checkText,
  draftProblem,
  saveLabel,
  shownValue,
  type Draft,
  type FieldRow,
} from "../model/fields.ts";
import { TextField } from "./fields.tsx";

interface Props {
  rows: FieldRow[];
  drafts: Record<string, Draft>;
  onDraft: (path: string, draft: Draft | null) => void;
  hovered: string | null;
  onHover: (path: string | null) => void;
  saving: boolean;
  onSave: () => void;
}

const BLANK = { value: "", absent: false, reason: "" };

export function FieldCheckTable({ rows, drafts, onDraft, hovered, onHover, saving, onSave }: Props) {
  return (
    <div className="flex flex-col gap-2">
      <Card className="py-0">
        <Table aria-label="Extracted fields">
          <TableHeader>
            <TableRow>
              <TableHead style={{ width: "30%" }}>Field</TableHead>
              <TableHead style={{ width: "30%" }}>Extracted</TableHead>
              <TableHead>Your check</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <FieldLine
                key={row.path}
                row={row}
                draft={drafts[row.path]}
                onDraft={(draft) => onDraft(row.path, draft)}
                hovered={hovered === row.path}
                onHover={onHover}
              />
            ))}
          </TableBody>
        </Table>
      </Card>
      <div>
        <Button disabled={saving || !canSave(rows, drafts)} onClick={onSave}>
          {saving ? "Saving…" : saveLabel(rows, drafts)}
        </Button>
      </div>
    </div>
  );
}

function FieldLine({
  row,
  draft,
  onDraft,
  hovered,
  onHover,
}: {
  row: FieldRow;
  draft: Draft | undefined;
  onDraft: (draft: Draft | null) => void;
  hovered: boolean;
  onHover: (path: string | null) => void;
}) {
  const absentId = useId();
  const verdict = draft?.verdict ?? null;
  const problem = draft === undefined ? null : draftProblem(draft);
  // Clicking the mark already chosen takes it back.
  const mark = (to: "correct" | "wrong") => onDraft(verdict === to ? null : { ...BLANK, ...draft, verdict: to });

  return (
    <TableRow
      tabIndex={0}
      onMouseEnter={() => onHover(row.path)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(row.path)}
      className={cn("even:bg-slate-50/60", hovered && "bg-blue-50 even:bg-blue-50 hover:bg-blue-50", verdict === "wrong" && "bg-red-50/60")}
    >
      <TableCell className="align-top">
        <code className="font-mono text-xs">{row.path}</code>
      </TableCell>
      <TableCell className={cn("align-top whitespace-normal", !row.found && "text-slate-400")}>{shownValue(row)}</TableCell>
      <TableCell className="align-top whitespace-normal">
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {draft === undefined && row.check !== null ? (
              <span className={cn("text-xs", row.check === "correct" ? "text-green-700" : "text-red-700")}>
                {checkText(row)}
              </span>
            ) : null}
            <Button size="sm" variant={verdict === "correct" ? "default" : "outline"} aria-pressed={verdict === "correct"} aria-label={`Correct: ${row.path}`} onClick={() => mark("correct")}>
              Correct
            </Button>
            <Button size="sm" variant={verdict === "wrong" ? "destructive" : "outline"} aria-pressed={verdict === "wrong"} aria-label={`Wrong: ${row.path}`} onClick={() => mark("wrong")}>
              Wrong
            </Button>
          </div>
          {draft !== undefined && draft.verdict === "wrong" ? (
            <div className="flex flex-col gap-2">
              <TextField
                label="Should be"
                value={draft.value}
                disabled={draft.absent}
                onChange={(value) => onDraft({ ...draft, value })}
              />
              <label htmlFor={absentId} className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  id={absentId}
                  type="checkbox"
                  checked={draft.absent}
                  onChange={(event) => onDraft({ ...draft, absent: event.target.checked })}
                />
                Not in the document
              </label>
              <TextField label="Reason" value={draft.reason} onChange={(reason) => onDraft({ ...draft, reason })} />
              {problem === null ? null : <p className="text-xs text-red-700">{problem}</p>}
            </div>
          ) : null}
        </div>
      </TableCell>
    </TableRow>
  );
}
