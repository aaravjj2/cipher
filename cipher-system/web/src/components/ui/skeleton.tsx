"use client";

/**
 * Loading placeholders shaped like the content that is coming.
 *
 * Nine panels previously signalled loading with a centred line of text. That is acceptable
 * for a fast fetch and poor for a slow one: Trident pulls three or four matrix payloads at
 * roughly 1.4 MB each and sits on "Loading live Trident data…" for ten to fifteen seconds,
 * which is long enough to read as a stall rather than as progress. A placeholder with the
 * shape of the eventual table also stops the layout jumping when data lands.
 *
 * Two things these deliberately get right, because a skeleton is easy to make actively worse
 * than the text it replaces:
 *
 * * It announces itself. A pure visual shimmer is invisible to a screen reader, so the
 *   wrapper carries `role="status"` and a real sentence, and the blocks themselves are
 *   `aria-hidden`. Without that, replacing text with boxes is a regression in accessibility.
 * * It respects `prefers-reduced-motion`. A grid of sixty pulsing cells is exactly the kind
 *   of large-area animation that setting exists for, so the pulse is dropped rather than
 *   merely slowed when it is set.
 */
import type { ReactNode } from "react";

/** One shimmer block. `aria-hidden` because `SkeletonBlock` alone conveys nothing useful. */
export function Skeleton({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <div
      aria-hidden="true"
      className={`cipher-skeleton rounded-[4px] ${className}`}
      style={{ background: "var(--panel-2)", ...style }}
    />
  );
}

/**
 * Wraps a set of blocks with the announcement a screen reader needs.
 * `label` should say what is loading, not merely that something is.
 */
export function SkeletonRegion({ label, children }: { label: string; children: ReactNode }) {
  return (
    // `cipher-skeleton-region` keeps this hidden for the first 250ms so a fetch that
    // resolves quickly never flashes a placeholder. That also means a screen reader is not
    // told "loading" for a load the user would not have noticed.
    <div role="status" aria-live="polite" className="cipher-skeleton-region flex flex-col gap-2">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

/**
 * A placeholder shaped like a strike/expiration grid: a header strip, then rows of cells.
 * Sized in `fr` so it occupies the same width the real grid will, which is what keeps the
 * panel from reflowing when the fetch resolves.
 */
export function SkeletonGrid({
  label,
  rows = 14,
  columns = 5,
}: {
  label: string;
  rows?: number;
  columns?: number;
}) {
  const template = `72px repeat(${columns}, minmax(0, 1fr))`;
  return (
    <SkeletonRegion label={label}>
      <div
        className="rounded-[10px] overflow-hidden"
        style={{ border: "1px solid var(--line)" }}
      >
        <div className="grid gap-px p-2" style={{ gridTemplateColumns: template }}>
          {Array.from({ length: columns + 1 }, (_, column) => (
            <Skeleton key={`h${column}`} className="h-[18px]" />
          ))}
        </div>
        <div className="grid gap-px px-2 pb-2" style={{ gridTemplateColumns: template }}>
          {Array.from({ length: rows * (columns + 1) }, (_, cell) => (
            <Skeleton
              key={cell}
              className="h-[22px]"
              // Fading down the page reads as depth rather than as sixty identical boxes,
              // and keeps the eye at the top where the first real data will appear.
              style={{ opacity: Math.max(0.25, 1 - Math.floor(cell / (columns + 1)) / rows) }}
            />
          ))}
        </div>
      </div>
    </SkeletonRegion>
  );
}

/**
 * A placeholder shaped like a chart beside its level rail, which is what Night Vision
 * resolves into. Deliberately not `SkeletonGrid`: a grid-shaped placeholder followed by a
 * candlestick chart moves the layout instead of holding it, which is most of the reason to
 * show a skeleton rather than a line of text.
 *
 * The bars step in height so the block reads as a chart rather than a solid panel. The
 * pattern is fixed rather than random so the placeholder does not reshuffle on re-render.
 */
export function SkeletonChart({ label, bars = 28, rows = 8 }: { label: string; bars?: number; rows?: number }) {
  return (
    <SkeletonRegion label={label}>
      <div className="flex flex-col lg:flex-row gap-3 items-stretch">
        <div
          className="flex flex-1 min-w-0 items-end gap-[3px] rounded-[10px] p-3"
          style={{ border: "1px solid var(--line)", background: "var(--panel)", minHeight: 260 }}
        >
          {Array.from({ length: bars }, (_, bar) => (
            <Skeleton
              key={bar}
              className="flex-1"
              // A smooth wave keyed off the index: recognisably chart-shaped without
              // implying any particular price action.
              style={{ height: `${38 + 42 * Math.abs(Math.sin(bar / 3.4))}%`, opacity: 0.55 }}
            />
          ))}
        </div>
        <div
          className="flex w-full lg:w-[240px] shrink-0 flex-col gap-2 rounded-[10px] p-3"
          style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
        >
          <Skeleton className="h-[12px] w-[52%]" />
          {Array.from({ length: rows }, (_, row) => (
            <Skeleton key={row} className="h-[16px]" style={{ opacity: Math.max(0.28, 1 - row / rows) }} />
          ))}
        </div>
      </div>
    </SkeletonRegion>
  );
}

/** A placeholder shaped like a list of cards, for panels that render stacked sections. */
export function SkeletonCards({
  label,
  count = 3,
  lines = 3,
}: {
  label: string;
  count?: number;
  lines?: number;
}) {
  return (
    <SkeletonRegion label={label}>
      {Array.from({ length: count }, (_, card) => (
        <div
          key={card}
          className="flex flex-col gap-2 rounded-[10px] p-4"
          style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
        >
          <Skeleton className="h-[14px] w-[38%]" />
          {Array.from({ length: lines }, (_, line) => (
            <Skeleton
              key={line}
              className="h-[11px]"
              style={{ width: `${88 - line * 13}%`, opacity: 0.7 }}
            />
          ))}
        </div>
      ))}
    </SkeletonRegion>
  );
}
