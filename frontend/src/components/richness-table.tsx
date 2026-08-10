"use client";

import { InfoHint } from "@/components/info-hint";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtDate, fmtNum, fmtSigned } from "@/lib/format";
import { RICHNESS_BG, RICHNESS_HINT, RICHNESS_TEXT, SKEW_BIAS_HINT, richnessKey } from "@/lib/theme";
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
            <TableHead className="text-right">Richness z</TableHead>
            <TableHead>
              Skew Bias <InfoHint text={SKEW_BIAS_HINT} />
            </TableHead>
            <TableHead>
              Richness <InfoHint text={RICHNESS_HINT} />
            </TableHead>
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
                <TableCell className="text-right font-mono tabular-nums">{fmtSigned(row.richness_z, 2)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {row.has_wing_data ? row.skew_bias : "—"}
                </TableCell>
                <TableCell>
                  {row.richness_z !== null && row.richness_label ? (
                    <span
                      className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                      style={{ backgroundColor: RICHNESS_BG[key], color: RICHNESS_TEXT[key] }}
                    >
                      {row.richness_label}
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.curvature, 2)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
