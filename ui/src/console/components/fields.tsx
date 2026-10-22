/**
 * Labelled controls for the console's forms.
 *
 * Each pairs a `<Label htmlFor>` with its control through `useId`, so every
 * field carries an accessible name (FR-045a) without each area wiring ids by
 * hand. Selects are native: Radix Select refuses an empty-string value, and
 * `languageChoices` offers "Parser default" as exactly that (ADR-0024).
 */

import { useEffect, useId, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

/** The same look as `Input`, on a native `<select>`. */
const SELECT_CLASS =
  "h-[26px] w-full min-w-0 rounded-md border border-input bg-white px-1.5 text-[13px] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50";

export function SelectField({ label, value, onChange, options, placeholder, className }: SelectFieldProps) {
  const id = useId();
  const choices = options.map((option) =>
    typeof option === "string" ? { value: option, label: option, disabled: false } : option,
  );
  const showPlaceholder =
    placeholder !== undefined && value === "" && !choices.some((choice) => choice.value === "");
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </Label>
      <select id={id} className={SELECT_CLASS} value={value} onChange={(event) => onChange(event.target.value)}>
        {showPlaceholder ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value} disabled={choice.disabled === true}>
            {choice.label}
          </option>
        ))}
      </select>
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
