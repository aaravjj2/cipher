export type PriceBarLike = { high: number; low: number };

export type NightVisionGeometry = {
  domainMin: number;
  domainMax: number;
  priceToY: (price: number) => number;
};

/**
 * Build one bounded price domain shared by candles, spot, exposure and session levels.
 * Levels may widen the candle domain, but never enough to flatten actual price action.
 */
export function buildNightVisionGeometry(
  bars: PriceBarLike[],
  spot: number,
  levels: Array<number | null | undefined>,
  top: number,
  height: number,
  levelDistancePct = 0.035,
  maxStretch = 2.2,
): NightVisionGeometry {
  const finiteBars = bars.flatMap((bar) => [bar.high, bar.low]).filter(Number.isFinite);
  const safeSpot = Number.isFinite(spot) && spot > 0 ? spot : (finiteBars[0] ?? 1);
  const candleMax = finiteBars.length ? Math.max(...finiteBars, safeSpot) : safeSpot;
  const candleMin = finiteBars.length ? Math.min(...finiteBars, safeSpot) : safeSpot;
  const barRange = Math.max(candleMax - candleMin, safeSpot * 0.001, 0.01);
  const nearby = levels
    .filter((value): value is number => value != null && Number.isFinite(value))
    .filter((value) => Math.abs(value - safeSpot) / safeSpot <= levelDistancePct);
  const stretchCap = Math.max(0, (barRange * maxStretch - barRange) / 2);
  const rawMax = Math.min(Math.max(candleMax, ...(nearby.length ? nearby : [candleMax])), candleMax + stretchCap);
  const rawMin = Math.max(Math.min(candleMin, ...(nearby.length ? nearby : [candleMin])), candleMin - stretchCap);
  const pad = Math.max((rawMax - rawMin) * 0.08, safeSpot * 0.0005, 0.01);
  const domainMax = rawMax + pad;
  const domainMin = rawMin - pad;
  return {
    domainMin,
    domainMax,
    priceToY: (price: number) => top + ((domainMax - price) / (domainMax - domainMin)) * height,
  };
}

export function nearestBarIndex(x: number, left: number, width: number, count: number): number | null {
  if (!Number.isFinite(x) || count <= 0 || width <= 0 || x < left || x > left + width) return null;
  const slot = width / count;
  return Math.max(0, Math.min(count - 1, Math.floor((x - left) / slot)));
}

export function visibleTail<T>(rows: T[], count: number): T[] {
  return rows.slice(-Math.max(1, Math.floor(count)));
}

export function isRegularSessionBar(iso: string): boolean {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return false;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const hour = Number(parts.find((part) => part.type === "hour")?.value);
  const minute = Number(parts.find((part) => part.type === "minute")?.value);
  const total = hour * 60 + minute;
  return total >= 570 && total < 960;
}
