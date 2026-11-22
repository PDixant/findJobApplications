import { useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";

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
};

/** A sortable table where a row opens its details (click, or Enter on a focused row). */
export function Table<T>({
  data, columns, rowKey, selected, onOpen, initialSort = [], rowClass, sortable = true, flexColumn = "title",
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
      <table className="data">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((h) => {
                const can = sortable && h.column.getCanSort();
                const dir = h.column.getIsSorted();
                return (
                  <th
                    key={h.id}
                    style={{ width: h.column.id === flexColumn ? undefined : `${h.column.getSize()}px` }}
                    aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}
                  >
                    {can ? (
                      <button className="th-sort" onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <span className="sort-mark" aria-hidden="true">
                          {dir === "asc" ? "▲" : dir === "desc" ? "▼" : ""}
                        </span>
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
            <tr
              key={row.id}
              className={`${row.id === selected ? "is-selected" : ""} ${rowClass ? rowClass(row.original) : ""}`}
              tabIndex={onOpen ? 0 : undefined}
              onClick={(e) => {
                // links open the posting; the inline controls edit in place
                if ((e.target as HTMLElement).closest("a, select, input, textarea, button")) return;
                onOpen?.(row.original);
              }}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === "Enter" && onOpen) onOpen(row.original);
              }}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
