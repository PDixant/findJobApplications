import { useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { Icon, Skeleton } from "./bits";

type Props<T> = {
  data: T[];
  columns: ColumnDef<T, any>[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  rowKey: (row: T) => string;
  selected?: string | null;
  onOpen?: (row: T) => void;
  initialSort?: SortingState;
  rowClass?: (row: T) => string;
  sortable?: boolean;
  flexColumn?: string; // takes the width left over; every other column keeps its size
  label: string;
};

/** Numbers right-aligned in tabular figures: set meta: { num: true } on the column. */
const isNum = (meta: unknown) => Boolean((meta as { num?: boolean } | undefined)?.num);

/** A crisp, sortable table on the paper. A row opens its details (click, or Enter on a focused row). */
export function Table<T>({
  data, columns, rowKey, selected, onOpen, initialSort = [], rowClass, sortable = true, flexColumn = "title", label,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: sortable ? getSortedRowModel() : undefined,
    manualSorting: !sortable,
    getRowId: (row) => rowKey(row),
  });

  return (
    <div className="table-wrap">
      <table className="data" aria-label={label}>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((h) => {
                const can = sortable && h.column.getCanSort();
                const dir = h.column.getIsSorted();
                return (
                  <th key={h.id} className={`col-${h.column.id} ${isNum(h.column.columnDef.meta) ? "num-col" : ""}`}
                    style={{ width: h.column.id === flexColumn ? undefined : `${h.column.getSize()}px` }}
                    aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}>
                    {can ? (
                      <button className="th-sort" data-dir={dir || undefined} onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {dir && <Icon name={dir === "asc" ? "up" : "down"} size={12} />}
                      </button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className={`${row.id === selected ? "is-selected" : ""} ${rowClass ? rowClass(row.original) : ""}`}
              tabIndex={onOpen ? 0 : undefined}
              aria-selected={onOpen ? row.id === selected : undefined}
              onClick={(e) => {
                // links open the posting; the inline controls edit in place
                if ((e.target as HTMLElement).closest("a, select, input, textarea, button")) return;
                onOpen?.(row.original);
              }}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === "Enter" && onOpen) onOpen(row.original);
              }}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className={`col-${cell.column.id} ${isNum(cell.column.columnDef.meta) ? "num-col" : ""}`}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The table's shape while it loads. */
export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div style={{ display: "grid", gap: 24, paddingTop: 16 }} aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} height={40} />)}
    </div>
  );
}
