/**
 * A succeeded OCR run's result: the page image with each line's box drawn
 * over it, beside the recognised text. Hovering either side highlights the
 * other. Every decision — which page comes next, whether a page was turned,
 * how a confidence reads — is `console/model/result.ts`'s.
 *
 * Page images are fetched as each page is first shown, held as blob URLs,
 * and all revoked when the viewer goes.
 */

import { useEffect, useRef, useState } from "react";
import { Banner, Button, Card, HStack, Stack, Text } from "@astryxdesign/core";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import {
  confidenceLabel,
  isTurned,
  pageAfter,
  TURNED_NOTICE,
  type ResultPage,
} from "../model/result.ts";

export type ImageRequest = (
  intent: Intent,
) => Promise<{ ok: true; blob: Blob } | { ok: false; failure: Failure }>;

interface Props {
  runId: string;
  pages: ResultPage[];
  request: ImageRequest;
}

type Image = { url: string } | { failure: Failure };

export function ResultViewer({ runId, pages, request }: Props) {
  const [shown, setShown] = useState(0);
  const [images, setImages] = useState<Map<number, Image>>(new Map());
  const [sizes, setSizes] = useState<Map<number, { width: number; height: number }>>(new Map());
  const [hovered, setHovered] = useState<number | null>(null);
  const urls = useRef<string[]>([]);
  const lineRefs = useRef(new Map<number, HTMLLIElement>());

  // Revoke every URL this viewer made, once, when it goes.
  useEffect(
    () => () => {
      urls.current.forEach((url) => URL.revokeObjectURL(url));
      urls.current = [];
    },
    [],
  );

  const page = pages[shown];
  const pageIndex = page?.index;
  const fetched = pageIndex === undefined || images.has(pageIndex);

  useEffect(() => {
    if (pageIndex === undefined || fetched) return;
    let current = true;
    void request({ type: "run-page", runId, page: pageIndex }).then((answer) => {
      if (!current) return;
      let image: Image;
      if (answer.ok) {
        const url = URL.createObjectURL(answer.blob);
        urls.current.push(url);
        image = { url };
      } else {
        image = { failure: answer.failure };
      }
      setImages((previous) => new Map(previous).set(pageIndex, image));
    });
    return () => {
      current = false;
    };
  }, [request, runId, pageIndex, fetched]);

  if (page === undefined) return null;

  const image = images.get(page.index);
  const size = sizes.get(page.index);
  const turned = size !== undefined && isTurned(page, size.width, size.height);
  const previous = pageAfter(shown, pages.length, -1);
  const next = pageAfter(shown, pages.length, 1);

  const hoverFromBox = (index: number) => {
    setHovered(index);
    lineRefs.current.get(index)?.scrollIntoView({ block: "nearest" });
  };

  return (
    <Stack direction="vertical" gap={2}>
      <HStack gap={2} align="center">
        <Button
          label="Previous"
          variant="secondary"
          isDisabled={previous === null}
          onClick={() => previous !== null && setShown(previous)}
        />
        <Text size="sm">
          Page {shown + 1} of {pages.length}
        </Text>
        <Button
          label="Next"
          variant="secondary"
          isDisabled={next === null}
          onClick={() => next !== null && setShown(next)}
        />
      </HStack>

      <HStack gap={3} align="start">
        <div style={{ flex: "0 1 55%", maxWidth: "55%", minWidth: 0 }}>
          <div role="status" aria-live="polite">
            {turned ? <Banner status="warning" title={TURNED_NOTICE} /> : null}
            {image !== undefined && "failure" in image ? (
              <Banner
                status="error"
                title="The page image could not be read"
                description={describe(image.failure)}
              />
            ) : null}
            {image === undefined ? <Text size="sm">Loading…</Text> : null}
          </div>
          {image !== undefined && "url" in image ? (
            <div style={{ position: "relative" }}>
              <img
                src={image.url}
                alt={`Page ${shown + 1}`}
                style={{ display: "block", width: "100%", height: "auto" }}
                onLoad={(event) => {
                  const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
                  setSizes((previous) => new Map(previous).set(page.index, { width, height }));
                }}
              />
              <svg
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
                aria-hidden="true"
                style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
              >
                {page.lines.map((line) =>
                  line.bbox === null ? null : (
                    <rect
                      key={line.index}
                      x={line.bbox[0]}
                      y={line.bbox[1]}
                      width={line.bbox[2] - line.bbox[0]}
                      height={line.bbox[3] - line.bbox[1]}
                      fill={hovered === line.index ? "var(--color-accent)" : "transparent"}
                      fillOpacity={hovered === line.index ? 0.25 : 1}
                      stroke="var(--color-accent)"
                      strokeWidth={1.5}
                      vectorEffect="non-scaling-stroke"
                      onMouseEnter={() => hoverFromBox(line.index)}
                      onMouseLeave={() => setHovered(null)}
                    />
                  ),
                )}
              </svg>
            </div>
          ) : null}
        </div>

        <div style={{ flex: "1 1 0", minWidth: 0 }}>
          <Card>
            <ul
              aria-label={`Recognised text, page ${shown + 1}`}
              style={{ listStyle: "none", margin: 0, padding: 0, maxHeight: "70vh", overflowY: "auto" }}
            >
              {page.lines.map((line) => (
                <li
                  key={line.index}
                  ref={(element) => {
                    if (element === null) lineRefs.current.delete(line.index);
                    else lineRefs.current.set(line.index, element);
                  }}
                  tabIndex={0}
                  onMouseEnter={() => setHovered(line.index)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(line.index)}
                  onBlur={() => setHovered(null)}
                  style={{
                    padding: "6px 8px",
                    borderRadius: 4,
                    background: hovered === line.index ? "var(--color-accent-muted)" : "transparent",
                  }}
                >
                  <HStack gap={2} align="center" justify="between">
                    <Text size="sm">{line.text}</Text>
                    <Text size="sm" color="secondary">
                      {confidenceLabel(line.confidence)}
                    </Text>
                  </HStack>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </HStack>
    </Stack>
  );
}
