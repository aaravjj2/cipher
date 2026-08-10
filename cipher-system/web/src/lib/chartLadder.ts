/**
 * Geometry for the Chart Saves thumbnail: the card's own numbers placed on one shared
 * price axis.
 *
 * This lives in lib/ rather than inside the panel so it can be tested directly. The bug
 * worth guarding against is not cosmetic — the previous thumbnail drew candles in raw
 * 0..100 space while drawing level lines in price space, so two marks at the same height
 * meant two unrelated things and the picture implied a price/level interaction that had
 * never been measured. `yFor` being the single mapping used for every mark is the fix,
 * and the tests assert exactly that.
 */

export const CHART_W = 320;
export const CHART_H = 200;
const PAD_Y = 24;
export const LABEL_W = 62;

/**
 * Only the two fields the geometry actually reads. Declared structurally instead of
 * importing ChartSaveCard so this module has no `@/` alias dependency and its tests can
 * compile it with a bare tsc — the same reason lib/markdown.ts stays dependency-free.
 * ChartSaveCard satisfies this shape.
 */
export type LadderInput = {
  price: number;
  topLevels: { level: number; score: number }[];
};

export type Ladder = {
  /** Price → y in the SVG's coordinate space. Lower y is higher on screen. */
  yFor: (price: number) => number;
  /** Score → bar width in px, relative to the strongest level on this card. */
  barFor: (score: number) => number;
  spotY: number;
};

export function buildLadder(card: LadderInput): Ladder {
  const levels = card.topLevels;
  const prices = [...levels.map((l) => l.level), card.price];
  const maxPrice = Math.max(...prices);
  const minPrice = Math.min(...prices);
  const span = maxPrice - minPrice;
  // A single level, or several at one price, has no meaningful axis — center it rather
  // than dividing by ~0 and flinging every mark to one edge.
  const flat = span < 1e-9;
  const yFor = (price: number) =>
    flat ? CHART_H / 2 : PAD_Y + ((maxPrice - price) / span) * (CHART_H - PAD_Y * 2);
  const maxScore = Math.max(...levels.map((l) => l.score), 1);
  const barFor = (score: number) =>
    Math.max(2, (Math.max(score, 0) / maxScore) * (CHART_W - LABEL_W - 16));
  return { yFor, barFor, spotY: yFor(card.price) };
}
