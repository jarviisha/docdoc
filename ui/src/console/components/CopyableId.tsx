/**
 * An identifier shown short, kept whole: the full value is in `title` and is
 * what the copy button copies. With `onOpen` the short form is also the link
 * to the thing it names.
 */

import { useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * The Clipboard API exists only in a secure context, and a console reached
 * over plain http is not one; the selection fallback works in both.
 */
async function copy(text: string): Promise<void> {
  if (navigator.clipboard !== undefined) return navigator.clipboard.writeText(text);
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

interface Props {
  value: string;
  /** What is shown; the whole value when absent. */
  display?: string;
  onOpen?: () => void;
}

export function CopyableId({ value, display, onOpen }: Props) {
  const [copied, setCopied] = useState(false);
  const shown = display ?? value;

  return (
    <span className="group inline-flex items-center gap-1" title={value}>
      {onOpen === undefined ? (
        <code className="font-mono text-xs">{shown}</code>
      ) : (
        <Button variant="link" className="h-auto p-0 font-mono text-xs" onClick={onOpen}>
          {shown}
        </Button>
      )}
      <button
        type="button"
        aria-label={`Copy ${value}`}
        className="rounded-sm p-0.5 text-slate-400 opacity-0 outline-none group-hover:opacity-100 hover:text-slate-700 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/50"
        onClick={() => {
          void copy(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          });
        }}
      >
        {copied ? <CheckIcon className="size-3 text-green-600" /> : <CopyIcon className="size-3" />}
      </button>
    </span>
  );
}
