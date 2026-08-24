import type { RealMatrixRow } from "@/lib/api";
import type { ExposureMetric } from "@/types/cipher";

/** Sum finite available cell nets for a strike. Null when nothing is known — never 0. */
export function knownStrikeNet(
  rows: RealMatrixRow[] | undefined,
  strike: number,
  metric: ExposureMetric,
): number | null {
  if (!rows?.length) return null;
  const row = rows.find((r) => r.strike === strike);
  if (!row) return null;
  let sum = 0;
  let any = false;
  for (const cell of row.cells) {
    const available =
      metric === "gex" ? (cell.gex_available ?? cell.available) : (cell.vex_available ?? cell.available);
    const raw = metric === "gex" ? cell.net_gex : cell.net_vex;
    if (!available || raw == null || !Number.isFinite(raw)) continue;
    sum += raw;
    any = true;
  }
  return any ? sum : null;
}
