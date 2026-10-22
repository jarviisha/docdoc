/** A typed table over the console's rows: one column definition per cell renderer. */

import type { ReactNode } from "react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export interface Column<T> {
  key: string;
  header: string;
  /** Any CSS width: `"180px"`, `"auto"`. */
  width?: string;
  cell: (row: T) => ReactNode;
}

interface Props<T> {
  rows: T[];
  rowKey: (row: T) => string;
  columns: Column<T>[];
  label: string;
}

export function DataTable<T>({ rows, rowKey, columns, label }: Props<T>) {
  return (
    <Table aria-label={label}>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead key={column.key} style={column.width === undefined ? undefined : { width: column.width }}>
              {column.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={rowKey(row)}>
            {columns.map((column) => (
              <TableCell key={column.key} className="py-1.5 whitespace-normal">
                {column.cell(row)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
