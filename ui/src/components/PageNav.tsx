/**
 * Reaching the pages the result never named.
 *
 * The viewer renders the pages carrying located values and no others, because
 * the deployment's default page limit is 1000 and eager rendering fails on
 * documents docdoc itself accepts (FR-051, FR-053). The bound is only honest if
 * the rest of the document stays reachable, which is this component (FR-052).
 *
 * The notice above it is the model's, not ours: a reader who takes "the pages
 * with values on them" for "the whole document" has been misled by a viewer
 * whose entire purpose is telling them what is really there (FR-054).
 */

import { Button, Stack, Text } from "@astryxdesign/core";

interface Props {
  notice: string | null;
  pages: number[];
  shown: number[];
  onRequest: (pageIndex: number) => void;
}

export function PageNav({ notice, pages, shown, onRequest }: Props) {
  return (
    <Stack direction="vertical" gap={2} aria-label="Pages">
      {notice === null ? null : <Text size="sm">{notice}</Text>}
      <Stack direction="horizontal" gap={1} wrap="wrap">
        {pages.map((pageIndex) => (
          <Button
            key={pageIndex}
            label={String(pageIndex + 1)}
            size="sm"
            variant={shown.includes(pageIndex) ? "primary" : "secondary"}
            onClick={() => onRequest(pageIndex)}
          />
        ))}
      </Stack>
    </Stack>
  );
}
