"use client";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtDate, fmtNum, fmtSigned } from "@/lib/format";
import { RICHNESS_BG, RICHNESS_TEXT, richnessKey } from "@/lib/theme";
import type { ExpiryScoreRow } from "@/lib/api";
import { cn } from "@/lib/utils";

export function RichnessTable({
  rows,
  onSelectExpiration,
}: {
  rows: ExpiryScoreRow[];
  onSelectExpiration?: (expiration: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No expiry data available.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Expiration</TableHead>
            <TableHead className="text-right">DTE</TableHead>
            <TableHead className="text-right">ATM IV</TableHead>
            <TableHead className="text-right">VRP z</TableHead>
            <TableHead>IV Richness</TableHead>
            <TableHead>Put/Call Skew</TableHead>
            <TableHead className="text-right">Curvature</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const key = richnessKey(row.richness_label);
            return (
              <TableRow
                key={row.expiration}
                className={cn(onSelectExpiration && "cursor-pointer")}
                onClick={() => onSelectExpiration?.(row.expiration)}
              >
                <TableCell className="font-medium">{fmtDate(row.expiration)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{row.dte}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.atm_iv, 2)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{fmtSigned(row.vrp_z, 2)}</TableCell>
                <TableCell>
                  <span
                    className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                    style={{ backgroundColor: RICHNESS_BG[key], color: RICHNESS_TEXT[key] }}
                  >
                    {row.richness_label}
                  </span>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{row.skew_bias}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.curvature, 2)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <p className="mt-3 text-xs text-muted-foreground">
        <strong className="font-medium text-foreground">IV Richness</strong> compares this expiry&apos;s implied
        vol to its own realized vol, ranked against the other expiries in view — Rich favors selling premium,
        Cheap favors buying.{" "}
        <strong className="font-medium text-foreground">Put/Call Skew</strong> is separate: within this expiry&apos;s
        smile, which side is priced richer relative to the other.
      </p>
    </div>
  );
}
