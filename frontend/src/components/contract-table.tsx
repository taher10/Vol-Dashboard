"use client";

import { useState } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import { InfoHint } from "@/components/info-hint";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtInt, fmtNum, fmtPct } from "@/lib/format";
import { COLOR_CALL, COLOR_PUT } from "@/lib/theme";
import type { ContractRow } from "@/lib/api";
import { cn } from "@/lib/utils";

function headerWithHint(label: string, hint?: string) {
  if (!hint) return label;
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <InfoHint text={hint} />
    </span>
  );
}

function numCol(
  key: keyof ContractRow,
  label: string,
  digits = 2,
  fmt: (v: number | null | undefined, digits?: number) => string = fmtNum,
  hint?: string
): ColumnDef<ContractRow> {
  return {
    accessorKey: key as string,
    header: () => headerWithHint(label, hint),
    cell: ({ getValue }) => <span className="font-mono tabular-nums">{fmt(getValue<number>(), digits)}</span>,
  };
}

const columns: ColumnDef<ContractRow>[] = [
  {
    accessorKey: "rank",
    header: "#",
    cell: ({ getValue }) => <span className="font-mono tabular-nums text-muted-foreground">{getValue<number>()}</span>,
  },
  {
    accessorKey: "optionType",
    header: "Type",
    cell: ({ getValue }) => {
      const t = getValue<string>();
      return (
        <span className="font-semibold" style={{ color: t === "CALL" ? COLOR_CALL : COLOR_PUT }}>
          {t}
        </span>
      );
    },
  },
  numCol("strikePrice", "Strike", 1, fmtNum),
  {
    accessorKey: "expiration",
    header: "Exp",
    cell: ({ getValue }) => <span className="text-xs text-muted-foreground">{getValue<string>()?.slice(0, 10)}</span>,
  },
  numCol("dte", "DTE", 0, fmtInt),
  numCol("bid", "Bid"),
  numCol("ask", "Ask"),
  numCol("mid", "Mid"),
  numCol("spread_pct", "Spread", 1, fmtPct),
  numCol("volume", "Vol", 0, fmtInt),
  numCol("openInterest", "OI", 0, fmtInt),
  numCol("delta", "Delta", 3),
  numCol("impliedVolatility", "IV", 2),
  numCol(
    "liquidity_score",
    "Liquidity",
    0,
    fmtNum,
    "Blend of tight bid/ask spread, volume, and open interest, ranked 0–100 within this expiry."
  ),
  numCol(
    "delta_fit_score",
    "Delta fit",
    0,
    fmtNum,
    "How close this contract's delta is to your target delta — 100 at the target, decaying to 0 at your tolerance band edge."
  ),
  numCol(
    "value_score",
    "Value",
    0,
    fmtNum,
    "Cheap/rich vs. this expiry's own smile — high if a buy looks underpriced (or a sell looks overpriced) relative to its neighbors."
  ),
  {
    accessorKey: "composite_score",
    header: () =>
      headerWithHint(
        "Score",
        "Weighted blend of Value, Delta fit, and Liquidity per your Settings sliders — higher is a better match for your stated intent."
      ),
    cell: ({ getValue }) => {
      const v = getValue<number | null>();
      return <span className="font-mono font-semibold tabular-nums">{fmtNum(v, 1)}</span>;
    },
  },
];

export function ContractTable({
  rows,
  selectedRank,
  onSelectRow,
  hideColumns = [],
}: {
  rows: ContractRow[];
  selectedRank?: number | null;
  onSelectRow?: (row: ContractRow) => void;
  hideColumns?: (keyof ContractRow)[];
}) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "rank", desc: false }]);

  const visibleColumns = columns.filter(
    (c) => !hideColumns.includes((c as ColumnDef<ContractRow> & { accessorKey: keyof ContractRow }).accessorKey)
  );

  const table = useReactTable({
    data: rows,
    columns: visibleColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (rows.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No contracts match the current filters.</p>;
  }

  return (
    <div className="max-h-[520px] overflow-auto rounded-md border border-border">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-card">
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((header) => {
                const sort = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    className="cursor-pointer select-none whitespace-nowrap"
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sort === "asc" && <ArrowUp className="size-3" />}
                      {sort === "desc" && <ArrowDown className="size-3" />}
                      {!sort && <ArrowUpDown className="size-3 opacity-30" />}
                    </span>
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              onClick={() => onSelectRow?.(row.original)}
              className={cn(
                onSelectRow && "cursor-pointer",
                selectedRank === row.original.rank && "bg-accent"
              )}
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id} className="whitespace-nowrap">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
