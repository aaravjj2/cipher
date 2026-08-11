// Content structures observed on https://www.accessobsidian.com/app
// See docs/research/accessobsidian.com/ for extraction source.

export type Quote = {
  ticker: string;
  price: number;
  changePct: number;
};

export type StrikeMatrixCell = {
  strike: number;
  expirationIso: string;
  value: number | null; // null means the contract/cell is unavailable; a listed zero remains 0
  /** Whether the backend had a listed/calculable cell at this strike and expiration. */
  available: boolean;
  /** Exposure leans on a reconstructed gamma and/or volume-as-open-interest. */
  modeled?: boolean;
};

export type StrikeMatrixExpiration = {
  dateLabel: string; // "Aug 7"
  daysLabel: string; // "1d"
  /** Real ISO expiration date ("2026-08-07"), when backed by live /api/matrix data. */
  iso?: string;
};

export type StrikeMatrixMode = "matrix" | "sniper";
export type ExposureMetric = "gex" | "vex";
export type MatrixDensity = "full" | "compact";

export type WatchlistItem = {
  ticker: string;
  changePct: number | null;
  changeAbs: number | null;
  price: number | null;
  compactScore: number | null;
};

export type ChartSaveCard = {
  id: string;
  ticker: string;
  price: number;
  view: string; // "1 Exp"
  dateAdded: string; // "7/23/26"
  topLevels: { level: number; score: number }[];
  imageUrl: string;
};

export type ScannerResultCard = {
  rank: number;
  ticker: string;
  direction: "bullish" | "bearish";
  score: number; // out of 100
  majorSupports: number[];
  majorResistances: number[];
  pullTarget: number;
  vacuumTargets: number[];
  cipherRead: string; // generated narrative text
};

export type SpyglassRow = {
  ticker: string;
  timeEt: string;
  sizePrem: number;
  contracts: number;
  px: number;
  strike: number;
  expiration: string;
  callPut: "C" | "P";
  bidAsk: string;
  pctOtm: number;
};

export type JournalDay = {
  date: string; // ISO date
  pnl: number | null;
};

export type NightVisionOverlay = "sp" | "spyQqq" | "ts" | "vp" | "xray";

export type UserSession = {
  displayName: string; // e.g. "Mars!"
  plan: "free" | "cipher-x";
};
