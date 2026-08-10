"use client";

import { useEffect, useMemo, useState } from "react";
import { buildLadder, CHART_H, CHART_W, LABEL_W } from "@/lib/chartLadder";
import { loadChartSaves, removeChartSave } from "@/lib/chartSaves";
import type { ChartSaveCard } from "@/types/cipher";

/**
 * Chart Saves panel — grid gallery of snapshots saved from Night Vision's "Save chart"
 * button (see NightVision.tsx), persisted to localStorage via lib/chartSaves.ts (no
 * server-side backend for this feature — same as the legacy vanilla-JS frontend).
 *
 * Every number on a card is real: ticker, saved spot price, and the scored levels. The
 * thumbnail draws only those, to scale. It used to draw a seeded random-walk
 * candlestick series instead, which was a fabricated price chart in all but name — and
 * because the candles were plotted in raw 0..100 space while the level lines were
 * plotted in price space, the picture implied price had reacted to a level when nothing
 * had been measured at all. There is no chart-image capture behind this feature, so the
 * honest thumbnail is the level ladder, not invented candles.
 */

/** Rank color per the app's purple/positive · red/negative convention (never green). */
function rankColor(index: number): string {
  if (index === 0) return "var(--gold)";
  if (index === 1) return "var(--accent)";
  return "var(--neg)";
}

function ChartThumbnail({ card }: { card: ChartSaveCard }) {
  const { yFor, barFor, spotY } = useMemo(() => buildLadder(card), [card]);
  const label = `${card.ticker} at $${card.price.toFixed(2)} with ${card.topLevels.length} scored levels`;

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      width="100%"
      height="100%"
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className="block"
    >
      {/* Saved spot price: solid, neutral, spanning the full width. */}
      <line
        x1={0}
        x2={CHART_W}
        y1={spotY}
        y2={spotY}
        style={{ stroke: "var(--text-dim)" }}
        strokeWidth={1}
        opacity={0.5}
      />
      <text
        x={CHART_W - 6}
        y={spotY - 5}
        textAnchor="end"
        style={{ fill: "var(--text-mute)", fontSize: 9, fontFamily: "var(--font-mono)" }}
      >
        spot {card.price.toFixed(2)}
      </text>

      {card.topLevels.map((lvl, i) => {
        const y = yFor(lvl.level);
        const color = rankColor(i);
        return (
          <g key={`${lvl.level}-${i}`}>
            <rect
              x={LABEL_W}
              y={y - 3}
              width={barFor(lvl.score)}
              height={6}
              style={{ fill: color }}
              opacity={0.75}
              rx={1}
            />
            <text
              x={LABEL_W - 6}
              y={y + 3.5}
              textAnchor="end"
              style={{ fill: color, fontSize: 10, fontFamily: "var(--font-mono)" }}
            >
              {lvl.level}
            </text>
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
