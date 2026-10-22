/** A typed table over the console's rows: one column definition per cell renderer. */

import type { ReactNode } from "react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export interface Column<T> {
  key: string;
  header: string;
  /** Any CSS width: `"180px"`, `"auto"`. */
  width?: string;
  /** Numbers and durations read down a column when they share a right edge. */
  align?: "right";
  cell: (row: T) => ReactNode;
}

interface Props<T> {
  rows: T[];
  rowKey: (row: T, index: number) => string;
  columns: Column<T>[];
  label: string;
}

export function DataTable<T>({ rows, rowKey, columns, label }: Props<T>) {
  return (
    <Table aria-label={label}>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead
              key={column.key}
              className={column.align === "right" ? "text-right" : undefined}
              style={column.width === undefined ? undefined : { width: column.width }}
            >
              {column.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) => (
          <TableRow key={rowKey(row, index)} className="even:bg-slate-50/60">
            {columns.map((column) => (
              <TableCell key={column.key} className={column.align === "right" ? "text-right tabular-nums" : "whitespace-normal"}>
                {column.cell(row)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
