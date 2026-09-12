"use client";

/** Pipeline coverage grid -- one row per symbol, one cell per expected
 * trading day. A filled cell means that symbol recorded metrics that day; an
 * empty cell means it didn't. Deliberately not a chart: the thing worth
 * seeing here is the *shape* of the holes (a vertical band of empties = the
 * whole pipeline was down; a single sparse row = one symbol is lagging),
 * and that reads instantly off a grid in a way it doesn't off any
 * aggregate. */

/** 12px cell + 1px gap -- label positioning has to match the grid's pitch. */
const CELL_PITCH = 13;

export interface CoverageRow {
  symbol: string;
  color: string;
  covered_days: string[];
}

export function CoverageHeatmap({
  calendar,
  rows,
  reportingPerDay,
  symbolCount,
}: {
  calendar: string[];
  rows: CoverageRow[];
  reportingPerDay: Record<string, number>;
  symbolCount: number;
}) {
  if (rows.length === 0 || calendar.length === 0) {
    return <div className="py-8 text-center text-sm text-muted-foreground">No coverage history yet.</div>;
  }

  // Label roughly every 5th column so the axis stays readable at 45 columns.
  const labelEvery = Math.max(1, Math.ceil(calendar.length / 9));

  return (
    <div className="overflow-x-auto">
      <div className="min-w-max">
        {/* Labels are absolutely positioned rather than laid out in the grid:
            a date string is far wider than the 13px column pitch, so in flow
            it would either clip or force the columns apart. */}
        <div className="relative mb-1 ml-14 h-3.5" style={{ width: calendar.length * CELL_PITCH }}>
          {calendar.map((day, i) =>
            i % labelEvery === 0 ? (
              <span
                key={day}
                className="absolute top-0 whitespace-nowrap text-[9px] text-muted-foreground"
                style={{ left: i * CELL_PITCH }}
              >
                {day.slice(5)}
              </span>
            ) : null
          )}
        </div>

        <div className="mb-1.5 flex items-center gap-px">
          <span className="w-14 shrink-0 pr-1 text-right text-[9px] uppercase tracking-wide text-muted-foreground">
            All
          </span>
          {calendar.map((day) => {
            const n = reportingPerDay[day] ?? 0;
            const frac = symbolCount > 0 ? n / symbolCount : 0;
            return (
              <div
                key={day}
                title={`${day} — ${n} of ${symbolCount} symbols reported`}
                className="h-3 w-3 shrink-0 rounded-[2px] bg-foreground"
                style={{ opacity: frac === 0 ? 0.06 : 0.25 + frac * 0.75 }}
              />
            );
          })}
        </div>

        {rows.map((row) => {
          const covered = new Set(row.covered_days);
          return (
            <div key={row.symbol} className="flex items-center gap-px">
              <span className="w-14 shrink-0 pr-1 text-right font-mono text-[10px] text-muted-foreground">
                {row.symbol}
              </span>
              {calendar.map((day) => {
                const has = covered.has(day);
                return (
                  <div
                    key={day}
                    title={`${row.symbol} · ${day} — ${has ? "recorded" : "no data"}`}
                    className="h-3 w-3 shrink-0 rounded-[2px]"
                    style={{
                      backgroundColor: has ? row.color : "currentColor",
                      opacity: has ? 1 : 0.07,
                    }}
                  />
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
