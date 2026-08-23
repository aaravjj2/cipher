export type GuestPanelMode = "live" | "hybrid" | "demo" | "locked";

export type GuestPanelDefinition = {
  label: string;
  section: "TODAY" | "DISCOVER" | "ANALYZE" | "PLAN" | "REVIEW" | "LABS";
  mode: GuestPanelMode;
};

export const GUEST_TICKERS = [
  "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN",
  "GOOGL", "META", "TSLA", "AMD", "MU", "AVGO",
] as const;

export const GUEST_TICKER_SET: ReadonlySet<string> = new Set(GUEST_TICKERS);

export const GUEST_PANEL_CATALOG: readonly GuestPanelDefinition[] = [
  { label: "Autopilot", section: "TODAY", mode: "demo" },
  { label: "Morning Brief", section: "TODAY", mode: "demo" },
  { label: "Earnings Radar", section: "TODAY", mode: "demo" },
  { label: "Research Desk", section: "TODAY", mode: "demo" },
  { label: "Setup Scanner", section: "DISCOVER", mode: "demo" },
  { label: "My Watchlists", section: "DISCOVER", mode: "locked" },
  { label: "News", section: "DISCOVER", mode: "demo" },
  { label: "Ticker Workbench", section: "ANALYZE", mode: "hybrid" },
  { label: "Night Vision", section: "ANALYZE", mode: "hybrid" },
  { label: "Options Terminal", section: "ANALYZE", mode: "demo" },
  { label: "Strike Matrix", section: "ANALYZE", mode: "hybrid" },
  { label: "Spyglass", section: "ANALYZE", mode: "demo" },
  { label: "Company Context", section: "ANALYZE", mode: "demo" },
  { label: "Ask Cipher", section: "ANALYZE", mode: "locked" },
  { label: "Chart Workbench", section: "PLAN", mode: "demo" },
  { label: "Portfolio Risk", section: "PLAN", mode: "demo" },
  { label: "Holdings", section: "PLAN", mode: "locked" },
  { label: "Alerts", section: "PLAN", mode: "locked" },
  { label: "Paper Portfolios", section: "REVIEW", mode: "demo" },
  { label: "Trader Journal", section: "REVIEW", mode: "locked" },
  { label: "Standing", section: "REVIEW", mode: "demo" },
  { label: "Strategies", section: "REVIEW", mode: "demo" },
  { label: "Backtest", section: "LABS", mode: "demo" },
  { label: "Options Backtest", section: "LABS", mode: "demo" },
  { label: "GEX Replay", section: "LABS", mode: "demo" },
  { label: "Trident", section: "LABS", mode: "demo" },
  { label: "Chart Saves", section: "LABS", mode: "locked" },
  { label: "Beliefs", section: "LABS", mode: "demo" },
] as const;

export const GUEST_PANEL_BY_LABEL = new Map(
  GUEST_PANEL_CATALOG.map((panel) => [panel.label, panel] as const),
);

export const GUEST_PANEL_LABELS: ReadonlySet<string> = new Set(GUEST_PANEL_BY_LABEL.keys());

export function guestPanelMode(label: string): GuestPanelMode | null {
  return GUEST_PANEL_BY_LABEL.get(label)?.mode ?? null;
}
