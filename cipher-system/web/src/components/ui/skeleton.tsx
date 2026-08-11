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
    <div role="status" aria-live="polite" className="flex flex-col gap-2">
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
