// KPI sparkline data (DESIGN §9.3: a sparkline on every desktop tile).

/** Sparkline series for each tile from the stats series (strings, Decimal-safe). */
export function kpiSparks(month: { expense: string; income: string; net: string }[], week: { expense: string }[]) {
  return {
    today: week.map((p) => p.expense),
    month: month.map((p) => p.expense),
    income: month.map((p) => p.income),
    net: month.map((p) => p.net),
  }
}
