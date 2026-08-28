/**
 * One page, rendered to a canvas.
 *
 * **No rotation is applied here, and that is the whole content of this file.**
 * pdf.js's default viewport already applies the page's `/Rotate`, and docdoc's
 * coordinates are already in *displayed* space — `pdf_text.py` maps every word
 * box through `page.rotation_matrix` before normalizing against `page.rect`, and
 * the other two adapters record `rotation = 0` because their services report
 * displayed coordinates already (research R1).
 *
 * So `Page.rotation` exists on the wire and must never be read here. Reading it
 * and rotating is the plausible wrong implementation: it double-rotates, and it
 * fails only on rotated pages, which is the population FR-024 protects. Nothing
 * in this repository would catch it — the rendering layer has no automated test
 * at all — which is why the warning lives in the code rather than in a document.
 */

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

interface Props {
  pdf: PDFDocumentProxy;
  pageIndex: number;
  width: number;
  children?: React.ReactNode;
}

export function Page({ pdf, pageIndex, width, children }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [height, setHeight] = useState(0);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const page = await pdf.getPage(pageIndex + 1);
      // The default viewport. `rotation` is deliberately not passed: pdf.js
      // applies the page's own, which is the orientation docdoc's coordinates
      // were normalized against.
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: width / base.width });

      const canvas = canvasRef.current;
      if (cancelled || canvas === null) return;

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setHeight(viewport.height);

      const context = canvas.getContext("2d");
      if (context === null) return;
      await page.render({ canvas, canvasContext: context, viewport }).promise;
    })();

    return () => {
      cancelled = true;
    };
  }, [pdf, pageIndex, width]);

  return (
    <div style={{ position: "relative", width, height, marginBottom: "1rem" }}>
      <canvas ref={canvasRef} style={{ display: "block", width: "100%" }} />
      {children}
    </div>
  );
}
