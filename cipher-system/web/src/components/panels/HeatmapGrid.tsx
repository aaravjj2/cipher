import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";

/**
 * Shared heatmap primitives for Strike Matrix and Trident (and any future strike-heatmap
 * panel). Both render (strike, signed-dollar-exposure) grids and must stay visually
 * identical, so the color-scale formula and the cell/row building blocks live here once
 * instead of being copy-pasted per panel. Extracted from `StrikeMatrix.tsx` — see
 * `docs/research/components/strike-matrix.spec.md` and `trident.spec.md`.
 */

export function formatDollar(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

/**
 * Heatmap color scale (the single most important visual detail per spec).
 * Positive values interpolate toward `--accent` (purple), negative toward `--neg` (red),
 * both mixed against `--panel` so near-zero cells stay a barely-tinted dark background.
 * The mix percentage uses a sub-linear (sqrt-ish) easing curve on the magnitude ratio so
 * mid-size values are already clearly visible instead of the whole scale being dominated
 * by the single largest cell — this matches the screenshots, where six- and seven-figure
 * cells are already vividly saturated well before the absolute max.
 */
export function getCellColor(value: number, maxAbs: number): string {
  if (maxAbs <= 0 || value === 0) return "var(--panel)";
  const ratio = Math.min(Math.abs(value) / maxAbs, 1);
  const eased = Math.pow(ratio, 0.42);
  const mixPct = 5 + eased * 80; // 5% (near-zero) .. 85% (near-max)
  const token = value > 0 ? "--accent" : "--neg";
  return `color-mix(in srgb, var(${token}) ${mixPct.toFixed(1)}%, var(--panel))`;
}

/** Standard colored data cell (`.cell.score[.star]`) — one per (strike, expiration/instrument). */
export function HeatmapCell({
  value,
  maxAbs,
  isStar = false,
  modeled = false,
  style,
}: {
  value: number;
  maxAbs: number;
  isStar?: boolean;
  /** Marks a cell whose exposure was reconstructed rather than read from the feed. */
  modeled?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      className={cn("cell score", isStar && "star", modeled && "modeled")}
      title={
        modeled
          ? "Reconstructed: gamma solved from the option mid price and/or open interest substituted by session volume."
          : undefined
      }
      style={{
        margin: "1px",
        padding: "0 8px",
        height: "26px",
        borderRadius: "3px",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        fontSize: "11px",
        fontWeight: 700,
        letterSpacing: "0.22px",
        background: isStar ? "var(--gold)" : getCellColor(value, maxAbs),
        color: isStar ? "rgb(21,16,0)" : "#ffffff",
        // Subtle, non-alarming: a dotted underline reads as "softer evidence" without
        // competing with the heat colouring that carries the actual signal.
        borderBottom: modeled
          ? "1px dotted color-mix(in srgb, currentColor 45%, transparent)"
          : "1px solid transparent",
        ...style,
      }}
    >
      {formatDollar(value)}
    </div>
  );
}

/** Sticky strike-price label (`.k-cell[.atm]`) — left column of a heatmap row. */
export function StrikeLabelCell({
  strike,
  isAtm = false,
  style,
}: {
  strike: number;
  isAtm?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      // Anchor for Strike Matrix's auto-snap-to-golden: rows render as fragments,
      // so this sticky label is the one element per strike that can be scrolled to.
      data-strike={strike}
      className={cn("k-cell", isAtm && "atm")}
      style={{
        position: "sticky",
        left: 0,
        zIndex: 10,
        background: "var(--bg)",
        padding: "0 10px 0 4px",
        height: "26px",
        display: "flex",
        justifyContent: "flex-end",
        alignItems: "center",
        fontSize: "11px",
        fontWeight: isAtm ? 700 : 600,
        color: isAtm ? "#ffffff" : "var(--text-dim)",
        ...style,
      }}
    >
      {strike}
    </div>
  );
}

/**
 * Thin dashed marker row (`.spot-row`) showing the live spot price, absolutely overlaid
 * between the two data rows nearest the current price. Spans `columnCount` CSS Grid
 * columns of whatever grid it's placed inside (a full multi-expiration matrix, or a
 * narrow single-value column).
 */
export function SpotRow({
  spot,
  columnCount,
  label = "SPOT",
}: {
  spot: number;
  columnCount: number;
  label?: string;
}) {
  return (
    <div
      className="spot-row"
      style={{
        gridColumn: `1 / span ${columnCount}`,
        position: "relative",
        height: "14px",
      }}
    >
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: "50%",
          borderTop: "1px dashed var(--text-mute)",
          zIndex: 2,
        }}
      />
      <span
        className="spot-pill"
        style={{
          position: "sticky",
          left: "4px",
          zIndex: 15,
          display: "inline-block",
          transform: "translateY(-50%)",
          background: "var(--panel-2)",
          border: "1px solid var(--line)",
          borderRadius: "4px",
          padding: "1px 6px",
          fontSize: "10px",
          fontWeight: 700,
          letterSpacing: "0.04em",
          color: "var(--text)",
        }}
      >
        {label} {spot.toFixed(2)}
      </span>
    </div>
  );
}

/** Deterministic PRNG (mulberry32) so mock heatmap data is stable across server/client renders. */
export function mulberry32(seed: number) {
  let state = seed;
  return function next() {
    state |= 0;
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Index of the first strike <= spot in a descending strike list — the row the spot-row marker precedes. */
export function findSpotInsertIndex(descendingStrikes: number[], spot: number): number {
  const idx = descendingStrikes.findIndex((s) => s <= spot);
  return idx === -1 ? descendingStrikes.length : idx;
}
