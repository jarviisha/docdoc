/**
 * Labelled controls for the console's forms.
 *
 * Each pairs a `<Label htmlFor>` with its control through `useId`, so every
 * field carries an accessible name (FR-045a) without each area wiring ids by
 * hand.
 */

import { useEffect, useId, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

interface TextFieldProps {
  label: string;
  value: string;
  /** Absent for a read-only field. */
  onChange?: (value: string) => void;
  readOnly?: boolean;
  type?: string;
  placeholder?: string;
  autoComplete?: string;
  spellCheck?: boolean;
  disabled?: boolean;
  /** Called on Enter, for a field that applies itself without a form. */
  onEnter?: () => void;
  className?: string;
}

export function TextField({ label, onChange, onEnter, className, ...rest }: TextFieldProps) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </Label>
      <Input
        id={id}
        {...rest}
        onChange={(event) => onChange?.(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && onEnter !== undefined) onEnter();
        }}
      />
    </div>
  );
}

export type Option = string | { value: string; label: string; disabled?: boolean };

interface SelectFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  placeholder?: string;
  className?: string;
}

/** Smaller than the body text: a label names the box, it is not the content. */
const LABEL_CLASS = "text-xs font-normal text-slate-600";

/**
 * Radix Select refuses `""` as an item value, and "Parser default" is exactly
 * that. It is swapped for this token on the way in and back on the way out, so
 * the model never sees it.
 */
const EMPTY = "__docdoc_empty__";
const toItem = (value: string) => (value === "" ? EMPTY : value);
const fromItem = (value: string) => (value === EMPTY ? "" : value);

export function SelectField({ label, value, onChange, options, placeholder, className }: SelectFieldProps) {
  const id = useId();
  const choices = options.map((option) =>
    typeof option === "string" ? { value: option, label: option, disabled: false } : option,
  );
  // `""` with no `""` choice is "nothing chosen yet": Radix shows the placeholder.
  const hasEmpty = choices.some((choice) => choice.value === "");
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </Label>
      <Select value={value === "" && !hasEmpty ? "" : toItem(value)} onValueChange={(next) => onChange(fromItem(next))}>
        <SelectTrigger id={id} className="w-full">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent position="popper" className="w-(--radix-select-trigger-width)">
          {choices.map((choice) => (
            <SelectItem key={choice.value} value={toItem(choice.value)} disabled={choice.disabled === true}>
              {choice.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

interface FileFieldProps {
  label: string;
  value: File | null;
  onChange: (file: File | null) => void;
  accept?: string;
  className?: string;
}

export function FileField({ label, value, onChange, accept, className }: FileFieldProps) {
  const id = useId();
  // A file input cannot be cleared by setting its value; remounting it can.
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (value === null) setGeneration((g) => g + 1);
  }, [value]);
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </Label>
      <Input
        key={generation}
        id={id}
        type="file"
        {...(accept === undefined ? {} : { accept })}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
    </div>
  );
}

interface TextAreaFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  className?: string;
}

/** A multi-line text box: a prompt, a JSON document. Mono, because what it holds is machine text. */
export function TextAreaField({ label, value, onChange, rows = 16, className }: TextAreaFieldProps) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </Label>
      <textarea
        id={id}
        rows={rows}
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-md border border-input bg-white px-2 py-1 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
      />
    </div>
  );
}
