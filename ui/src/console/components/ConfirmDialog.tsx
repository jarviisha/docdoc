/**
 * The confirmation every destructive action goes through (ADR-0024).
 *
 * Radix traps focus inside, puts it on Cancel when the dialog opens, and
 * returns it to the trigger on close. The confirm button is a plain `Button`,
 * not `AlertDialogAction`, so a click does not close the dialog: the parent
 * closes it through `open` once the request settles, whichever way it went,
 * so a failure is never left behind the overlay.
 */

import { useState, type ReactNode } from "react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  /** What declining is called: "Keep running" says more than "Cancel" on a cancel. */
  cancelLabel?: string;
  confirmDisabled?: boolean;
  onConfirm: () => Promise<unknown>;
  children?: ReactNode;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  confirmDisabled = false,
  onConfirm,
  children,
}: Props) {
  // One request per confirmation: a double click sends it once.
  const [pending, setPending] = useState(false);

  return (
    // Held open while the request is in flight, as the inline confirms were:
    // the parent closes it when the answer arrives.
    <AlertDialog open={open} onOpenChange={(next) => (pending ? undefined : onOpenChange(next))}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description === undefined ? null : <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        {children}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{cancelLabel}</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={confirmDisabled || pending}
            onClick={() => {
              setPending(true);
              void onConfirm().finally(() => setPending(false));
            }}
          >
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
