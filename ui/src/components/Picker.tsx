/**
 * Choosing a document and a schema.
 *
 * **The document never leaves the browser except to the extraction endpoint**
 * (FR-032). It is read into memory, rendered here by pdf.js, and posted to
 * `POST /v1/extract`, which persists nothing (ADR-0012). It is not written to
 * `localStorage`, `sessionStorage`, IndexedDB or anywhere else — a rule now
 * enforced across the whole of `src/` by `scripts/check-readonly.mjs` rather
 * than promised in this comment. A reload therefore loses the view and the user
 * picks the file again, an accepted consequence recorded in the spec.
 *
 * The schema list comes from the deployment. When it is empty the model supplies
 * a sentence naming the setting that populates it, because "no schemas" is a
 * valid configuration and not a fault (FR-012, FR-026).
 */

import { Banner, Button, Selector, Stack, Text } from "@astryxdesign/core";

import { ACCEPTED } from "../model/document.ts";
import type { SchemaChoice } from "../model/schemas.ts";

interface Props {
  schemas: SchemaChoice[];
  emptyNotice: string | null;
  selectedSchema: string | null;
  canRun: boolean;
  onDocument: (file: File) => void;
  onSchema: (identity: string) => void;
  onRun: () => void;
}

export function Picker({
  schemas,
  emptyNotice,
  selectedSchema,
  canRun,
  onDocument,
  onSchema,
  onRun,
}: Props) {
  return (
    <Stack direction="vertical" gap={3}>
      <Stack direction="vertical" gap={1}>
        <Text size="sm" weight="bold">
          Document
        </Text>
        {/* `ACCEPTED` is the model's, beside the code that decides what to do
            with each type. The two were separate facts until T092 and they
            disagreed: this offered PNG and JPEG, and every file went to a PDF
            renderer that opens neither. */}
        {/* read-only-exempt: file picker — reads a local file the user chose and
            writes nothing. FR-013 needs one way for a document to enter the
            viewer; the platform offers exactly this one, and Astryx does not
            wrap it. */}
        <input
          type="file"
          accept={ACCEPTED}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file !== undefined) onDocument(file);
          }}
        />
      </Stack>

      {emptyNotice === null ? (
        <Selector
          label="Schema"
          placeholder="Choose…"
          options={schemas.map((schema) => schema.identity)}
          {...(selectedSchema === null ? {} : { value: selectedSchema })}
          onChange={(identity: string) => onSchema(identity)}
        />
      ) : (
        <Banner status="info" title="No schemas configured" description={emptyNotice} />
      )}

      <Button label="Extract" variant="primary" isDisabled={!canRun} onClick={onRun} />
    </Stack>
  );
}
