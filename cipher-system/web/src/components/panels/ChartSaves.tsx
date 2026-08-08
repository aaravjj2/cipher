"use client";

import { useEffect, useMemo, useState } from "react";
import { loadChartSaves, removeChartSave } from "@/lib/chartSaves";
import type { ChartSaveCard } from "@/types/cipher";

/**
 * Chart Saves panel — grid gallery of snapshots saved from Night Vision's "Save chart"
 * button (see NightVision.tsx), persisted to localStorage via lib/chartSaves.ts (no
 * server-side backend for this feature — same as the legacy vanilla-JS frontend). The
 * ticker/price/levels metadata on each card is real; the candlestick thumbnail is a
 * deterministic mock pattern seeded from the card id, since no real chart-image capture
 * exists yet — a reasonable placeholder rather than a fabricated data point.
 */

// ---------------------------------------------------------------------------
// Mock candlestick thumbnail
// ---------------------------------------------------------------------------

/** Deterministic PRNG (mulberry32) so mock data is stable across server/client renders. */
function mulberry32(seed: number) {
  let state = seed;
  return function next() {
    state |= 0;
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Simple string hash (djb2) so each card gets a stable-but-distinct PRNG seed. */
function hashSeed(input: string): number {
  let hash = 5381;
  for (let i = 0; i < input.length; i++) {
    hash = (hash * 33) ^ input.charCodeAt(i);
  }
  return hash >>> 0;
}

type Candle = { open: number; close: number; high: number; low: number };

/** Builds a random-walk candlestick series in 0..100 (y-space, 0 = top). */
function buildCandles(seed: number, count: number): Candle[] {
  const rng = mulberry32(seed);
  const candles: Candle[] = [];
  let cursor = 35 + rng() * 30; // start roughly mid-band
  for (let i = 0; i < count; i++) {
    const open = cursor;
    const drift = (rng() - 0.5) * 26;
    const close = Math.min(92, Math.max(8, open + drift));
    const wickUp = rng() * 8;
    const wickDown = rng() * 8;
    const high = Math.max(open, close) - wickUp;
    const low = Math.min(open, close) + wickDown;
    candles.push({ open, close, high: Math.max(high, 2), low: Math.min(low, 98) });
    cursor = close;
  }
  return candles;
}

/** Rank color per the app's purple/positive · red/negative convention (never green). */
function rankColor(index: number): string {
  if (index === 0) return "var(--gold)";
  if (index === 1) return "var(--accent)";
  return "var(--neg)";
}

const CHART_W = 320;
const CHART_H = 200;

function ChartThumbnail({ card }: { card: ChartSaveCard }) {
  const candles = useMemo(() => buildCandles(hashSeed(card.id), 13), [card.id]);
  const candleSlot = CHART_W / candles.length;
  const bodyWidth = candleSlot * 0.55;

  // Mirror the top two levels as faint dashed reference lines, colored by rank.
  const levelLines = card.topLevels.slice(0, 2);
  const prices = card.topLevels.map((l) => l.level);
  const maxPrice = Math.max(...prices, card.price);
  const minPrice = Math.min(...prices, card.price);
  const priceSpan = Math.max(maxPrice - minPrice, 1);
  const yForLevel = (level: number) => {
    const ratio = (level - minPrice) / priceSpan;
    return CHART_H * 0.85 - ratio * CHART_H * 0.7;
  };

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      width="100%"
      height="100%"
      preserveAspectRatio="none"
      aria-hidden="true"
      className="block"
    >
      {levelLines.map((lvl, i) => (
        <line
          key={lvl.level}
          x1={0}
          x2={CHART_W}
          y1={yForLevel(lvl.level)}
          y2={yForLevel(lvl.level)}
          style={{ stroke: rankColor(i) }}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.55}
        />
      ))}
      {candles.map((c, i) => {
        const cx = candleSlot * i + candleSlot / 2;
        const isUp = c.close <= c.open; // svg y grows downward: lower y (close) above open = "up"
        const color = isUp ? "var(--accent)" : "var(--neg)";
        const bodyTop = Math.min(c.open, c.close);
        const bodyHeight = Math.max(Math.abs(c.close - c.open), 2);
        return (
          <g key={i}>
            <line
              x1={cx}
              x2={cx}
              y1={c.high}
              y2={c.low}
              style={{ stroke: color }}
              strokeWidth={1}
            />
            <rect
              x={cx - bodyWidth / 2}
              y={bodyTop}
              width={bodyWidth}
              height={bodyHeight}
              style={{ fill: color }}
              rx={1}
            />
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-row items-center justify-between gap-2">
      <span
        className="text-[11px] font-bold uppercase"
        style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}
      >
        {label}
      </span>
      <span
        className="text-[13px] font-semibold text-right"
        style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}
      >
        {children}
      </span>
    </div>
  );
}

function Card({ card, onDelete }: { card: ChartSaveCard; onDelete: (id: string) => void }) {
  return (
    <div
      className="chart-save-card flex flex-col overflow-hidden"
      style={{
        background: "var(--panel)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius)",
      }}
    >
      {/* Thumbnail */}
      <div
        className="relative w-full"
        style={{ aspectRatio: "16 / 10", background: "var(--panel-2)" }}
      >
        <ChartThumbnail card={card} />
        <button
          type="button"
          onClick={() => onDelete(card.id)}
          aria-label={`Delete saved chart for ${card.ticker}`}
          className="absolute top-2 right-2 grid place-items-center w-[24px] h-[24px] rounded-full transition-colors duration-150 hover:brightness-125"
          style={{ background: "rgba(7,9,15,0.65)", color: "var(--text)" }}
        >
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
            <path
              fill="currentColor"
              d="M18.3 5.71 12 12.01l-6.3-6.3-1.41 1.41 6.3 6.3-6.3 6.3 1.41 1.41 6.3-6.3 6.3 6.3 1.41-1.41-6.3-6.3 6.3-6.3z"
            />
          </svg>
        </button>
      </div>

      {/* Metadata */}
      <div className="flex flex-col gap-[6px]" style={{ padding: "16px" }}>
        <MetaRow label="Date Added">{card.dateAdded}</MetaRow>
        <MetaRow label="Ticker">
          <span style={{ color: "var(--accent)" }}>${card.ticker}</span>
        </MetaRow>
        <MetaRow label="Price">${card.price.toFixed(2)}</MetaRow>
        <MetaRow label="View">{card.view}</MetaRow>

        {/* Top Levels */}
        <div className="flex flex-col gap-[4px] mt-2">
          <div className="flex flex-row items-center justify-between">
            <span
              className="text-[11px] font-bold uppercase"
              style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}
            >
              Top Levels
            </span>
            <span
              className="text-[11px] font-bold uppercase"
              style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}
            >
              Score
            </span>
          </div>
          {card.topLevels.map((lvl, i) => (
            <div key={`${lvl.level}-${i}`} className="flex flex-row items-center justify-between">
              <span
                className="text-[13px] font-bold"
                style={{ fontFamily: "var(--font-mono)", color: rankColor(i) }}
              >
                {lvl.level}
              </span>
              <span
                className="text-[13px] font-semibold"
                style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}
              >
                {lvl.score}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ChartSaves() {
  const [cards, setCards] = useState<ChartSaveCard[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setCards(loadChartSaves());
    setLoaded(true);
  }, []);

  const handleDelete = (id: string) => {
    setCards(removeChartSave(id));
  };

  return (
    <section className="chart-saves flex flex-col gap-6" style={{ color: "var(--text)" }}>
      <div className="flex flex-col gap-1">
        <h1 className="text-[28px] font-bold leading-tight">Chart Saves</h1>
        <p className="text-[14px]" style={{ color: "var(--text-mute)" }}>
          Snapshots you&apos;ve saved from Night Vision, with the top pull at capture time.
        </p>
      </div>

      {!loaded ? null : cards.length > 0 ? (
        <div
          className="grid gap-6"
          style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}
        >
          {cards.map((card) => (
            <Card key={card.id} card={card} onDelete={handleDelete} />
          ))}
        </div>
      ) : (
        <div
          className="flex items-center justify-center rounded-[10px] py-16 text-[14px]"
          style={{ border: "1px dashed var(--line)", color: "var(--text-mute)" }}
        >
          No saved charts yet.
        </div>
      )}
    </section>
  );
}

export default ChartSaves;
