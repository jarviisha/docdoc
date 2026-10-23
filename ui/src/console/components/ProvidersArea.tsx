/**
 * Which model adapters answer, in what order, with which model and key — for
 * the deployment default or one tenant's override, through
 * `/v1/console/providers`.
 *
 * **It decides nothing.** The view, the status, what the form starts from and
 * the bodies sent are `console/model/providers.ts`; what a failure says is
 * `console/model/failure.ts`. A typed key lives only in this form's state, is
 * sent in a body and cleared on save or cancel; no answer carries it back.
 */

import { useEffect, useState } from "react";
import { getRouteApi } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { describe } from "../model/failure.ts";
import {
  adapterUpdate,
  canSave,
  editable,
  isOverride,
  keyDisplay,
  keyPlaceholder,
  keySaved,
  movePriority,
  savedModel,
  SECRETS_NOTE,
  SOURCE_LABEL,
  statusOf,
  testBody,
  toProvidersView,
  toTestResult,
  withAdapter,
  withoutAdapter,
  type ProviderRow,
} from "../model/providers.ts";
import { tenantSearch } from "../model/search.ts";
import { toTenants } from "../model/submit.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { DataTable } from "./DataTable.tsx";
import { SelectField, TextField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { useAct, useIntent } from "./useIntent.ts";

const providersApi = getRouteApi("/providers");

const BLANK_FORM = { model: "", apiKey: "", clearKey: false };

/** No `tenant` in the URL is the deployment default; choosing one pushes it, so Back returns. */
export function ProvidersArea() {
  const tenant = providersApi.useSearch().tenant ?? null;
  const navigate = providersApi.useNavigate();
  const act = useAct();
  const tenants = useIntent({ type: "list-tenants" }, { select: toTenants });
  const list = useIntent({ type: "list-providers", tenantId: tenant }, { select: toProvidersView });
  const view = list.data ?? null;
  const [writeProblem, setWriteProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK_FORM);
  // The last "Test connection" answer, for the adapter it tested.
  const [tested, setTested] = useState<{ adapterId: string; ok: boolean; message: string } | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);

  const listProblem = list.isError ? describe(list.error) : null;
  const tenantsProblem = tenants.isError ? describe(tenants.error) : null;
  const editRow = view?.rows.find((row) => row.id === editing) ?? null;
  // An edited adapter gone from a refreshed answer: drop the form, typed key included.
  const editGone = view !== null && editing !== null && editRow === null;
  useEffect(() => {
    if (editGone) closeForm();
  }, [editGone]);

  const write = async (send: () => ReturnType<typeof act>) => {
    setWriteProblem(null);
    setBusy(true);
    const answer = await send();
    setBusy(false);
    if (!answer.ok) setWriteProblem(describe(answer.failure));
    return answer.ok;
  };

  const setPriority = (priority: string[]) =>
    void write(() =>
      act({ type: "set-provider-priority", tenantId: tenant }, JSON.stringify({ priority })),
    );

  const closeForm = () => {
    setEditing(null);
    setForm(BLANK_FORM);
    setTested(null);
  };

  const openForm = (row: ProviderRow) => {
    if (view === null) return;
    setEditing(row.id);
    setForm({ model: savedModel(view, row), apiKey: "", clearKey: false });
    setTested(null);
  };

  const save = async () => {
    if (editing === null) return;
    const body = JSON.stringify(adapterUpdate(form));
    if (await write(() => act({ type: "set-provider", tenantId: tenant, adapterId: editing }, body))) closeForm();
  };

  /** The form's values for the edited adapter; `{}` for one with nothing to edit (echo). */
  const test = async (adapterId: string, body: { model?: string; api_key?: string }) => {
    setTested(null);
    setTesting(true);
    const answer = await act({ type: "test-provider", tenantId: tenant, adapterId }, JSON.stringify(body));
    setTesting(false);
    setTested({
      adapterId,
      ...(answer.ok
        ? (toTestResult(answer.body) ?? { ok: false, message: "the test answered in an unexpected shape" })
        : { ok: false, message: describe(answer.failure) }),
    });
  };

  const reset = async () => {
    // The open form holds the override's values: saving it would put the override back.
    if (tenant !== null && (await write(() => act({ type: "reset-providers", tenantId: tenant })))) closeForm();
    setConfirmingReset(false);
  };

  const testLine = (result: { adapterId: string; ok: boolean; message: string }) => (
    <p className="flex items-center gap-2 text-xs">
      <Badge variant={result.ok ? "success" : "error"}>{result.ok ? "ok" : "failed"}</Badge>
      <code className="font-mono">{result.adapterId}</code>
      {result.message}
    </p>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end gap-2">
        <SelectField
          label="Scope"
          className="w-[280px]"
          options={[{ value: "", label: "(deployment default)" }, ...(tenants.data ?? [])]}
          value={tenant ?? ""}
          onChange={(chosen) =>
            void navigate({ search: (previous) => tenantSearch({ ...previous, tenant: chosen }) })
          }
        />
        <Button variant="outline" size="sm" disabled={list.isFetching} onClick={() => void list.refetch()}>
          Refresh
        </Button>
        <span className="flex-1" />
        {view !== null && tenant !== null && isOverride(view) ? (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => setConfirmingReset(true)}>
            Reset to deployment default
          </Button>
        ) : null}
      </div>

      <div role="status" aria-live="polite" className="empty:-mb-3">
        {tenantsProblem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>Tenants could not be loaded</AlertTitle>
            <AlertDescription>{tenantsProblem}</AlertDescription>
          </Alert>
        )}
        {listProblem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The providers could not be read</AlertTitle>
            <AlertDescription>{listProblem}</AlertDescription>
          </Alert>
        )}
        {writeProblem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The change was not saved</AlertTitle>
            <AlertDescription>{writeProblem}</AlertDescription>
          </Alert>
        )}
        {view === null && listProblem === null ? <p className="text-sm">Loading…</p> : null}
        {view !== null && view.rows.length === 0 ? (
          <Alert variant="info">
            <AlertTitle>This deployment has no model adapters to configure.</AlertTitle>
          </Alert>
        ) : null}
      </div>

      {view !== null && view.rows.length > 0 ? (
        <>
          <Card className="py-0">
            <DataTable
              rows={view.rows}
              rowKey={(row) => row.id}
              label="Model adapters, in priority order. Keys show only their last four characters."
              columns={[
                {
                  key: "position",
                  header: "#",
                  width: "40px",
                  align: "right",
                  cell: (row) => {
                    const i = view.priority.indexOf(row.id);
                    return i === -1 ? "—" : i + 1;
                  },
                },
                {
                  key: "id",
                  header: "Adapter",
                  width: "140px",
                  cell: (row) => (
                    <span className="flex items-center gap-2">
                      <code className="font-mono text-xs">{row.id}</code>
                      {row.testOnly ? <Badge variant="neutral">test only</Badge> : null}
                    </span>
                  ),
                },
                {
                  key: "model",
                  header: "Model",
                  width: "220px",
                  cell: (row) => (
                    <span className="flex flex-col">
                      <code className="font-mono text-xs">{row.model ?? "—"}</code>
                      <span className="text-xs text-slate-500">{SOURCE_LABEL[row.modelSource]}</span>
                    </span>
                  ),
                },
                {
                  key: "key",
                  header: "Key",
                  width: "160px",
                  cell: (row) => (
                    <span className="flex flex-col">
                      <code className="font-mono text-xs">{keyDisplay(row)}</code>
                      <span className="text-xs text-slate-500">{SOURCE_LABEL[row.keySource]}</span>
                    </span>
                  ),
                },
                {
                  key: "status",
                  header: "Status",
                  width: "auto",
                  cell: (row) => {
                    const status = statusOf(view, row);
                    return (
                      <span className="flex flex-wrap items-center gap-2">
                        <Badge variant={status.variant}>{status.label}</Badge>
                        {status.reason === null ? null : (
                          <span className="text-xs text-slate-600">{status.reason}</span>
                        )}
                      </span>
                    );
                  },
                },
                {
                  key: "actions",
                  header: "",
                  width: "300px",
                  cell: (row) => {
                    const listed = view.priority.includes(row.id);
                    return (
                      <span className="flex justify-end gap-1">
                        {listed ? (
                          <>
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label={`Move ${row.id} up`}
                              disabled={busy || view.priority[0] === row.id}
                              onClick={() => setPriority(movePriority(view.priority, row.id, -1))}
                            >
                              ↑
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label={`Move ${row.id} down`}
                              disabled={busy || view.priority.at(-1) === row.id}
                              onClick={() => setPriority(movePriority(view.priority, row.id, 1))}
                            >
                              ↓
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label={`Remove ${row.id} from priority`}
                              disabled={busy || view.priority.length <= 1}
                              onClick={() => setPriority(withoutAdapter(view.priority, row.id))}
                            >
                              Remove
                            </Button>
                          </>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            aria-label={`Add ${row.id} to priority`}
                            disabled={busy}
                            onClick={() => setPriority(withAdapter(view.priority, row.id))}
                          >
                            Add to priority
                          </Button>
                        )}
                        {editable(row) ? (
                          <Button
                            variant="outline"
                            size="sm"
                            aria-label={`Edit ${row.id}`}
                            disabled={busy}
                            onClick={() => openForm(row)}
                          >
                            Edit
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            aria-label={`Test ${row.id}`}
                            disabled={busy || testing}
                            onClick={() => void test(row.id, {})}
                          >
                            Test
                          </Button>
                        )}
                      </span>
                    );
                  },
                },
              ]}
            />
          </Card>
          <div role="status" aria-live="polite" className="empty:-mb-3">
            {tested === null || tested.adapterId === editing ? null : testLine(tested)}
          </div>
          <p className="text-xs text-slate-500">
            Priority from {SOURCE_LABEL[view.prioritySource]}. Workers started with --adapter use this list too
            once one is saved.
          </p>
        </>
      ) : null}

      {view !== null && editRow !== null ? (
        <Card className="max-w-[520px]">
          <CardContent className="flex flex-col gap-3">
            <SectionHeading>Edit {editRow.id}</SectionHeading>
            <TextField
              label="Model"
              placeholder={editRow.model === null ? "" : `${editRow.model} (${SOURCE_LABEL[editRow.modelSource]})`}
              spellCheck={false}
              value={form.model}
              onChange={(model) => setForm({ ...form, model })}
            />
            <div className="flex flex-col gap-1">
              <TextField
                label="API key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder={keyPlaceholder(view, editRow)}
                value={form.apiKey}
                disabled={form.clearKey}
                onChange={(apiKey) => setForm({ ...form, apiKey })}
              />
              {view.secretsConfigured ? null : <p className="text-xs text-amber-700">{SECRETS_NOTE}</p>}
              {/* Removing drops a typed key too: it would not be sent. */}
              {keySaved(view, editRow) ? (
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    aria-label="Remove saved key"
                    checked={form.clearKey}
                    onChange={(event) => setForm({ ...form, apiKey: "", clearKey: event.target.checked })}
                  />
                  Remove saved key
                </label>
              ) : null}
            </div>
            <div role="status" aria-live="polite" className="empty:-mb-3">
              {tested === null || tested.adapterId !== editRow.id ? null : testLine(tested)}
            </div>
            <div className="flex gap-2">
              <Button disabled={!canSave(form, view.secretsConfigured, busy || testing)} onClick={() => void save()}>
                {busy ? "Saving…" : "Save"}
              </Button>
              <Button variant="outline" disabled={busy || testing} onClick={() => void test(editRow.id, testBody(form))}>
                {testing ? "Testing…" : "Test connection"}
              </Button>
              <Button variant="ghost" disabled={busy || testing} onClick={closeForm}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <ConfirmDialog
        open={confirmingReset}
        onOpenChange={setConfirmingReset}
        title="Reset this tenant's providers?"
        description="Its saved priority, models and API keys are removed, and it uses the deployment default."
        confirmLabel="Reset"
        cancelLabel="Keep"
        confirmDisabled={busy}
        onConfirm={reset}
      />
    </div>
  );
}
