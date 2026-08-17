"use client";

import { memo } from "react";
import { StrikeMatrix as StrikeMatrixBase } from "@/components/panels/StrikeMatrix";
import { NightVision as NightVisionBase } from "@/components/panels/NightVision";
import { Spyglass as SpyglassBase, type SpyglassTab } from "@/components/panels/Spyglass";
import { News as NewsBase } from "@/components/panels/News";
import { Watchlists as WatchlistsBase } from "@/components/panels/Watchlists";
import { Standing as StandingBase } from "@/components/panels/Standing";
import { Beliefs as BeliefsBase } from "@/components/panels/Beliefs";
import { Holdings as HoldingsBase } from "@/components/panels/Holdings";
import { AskCipher as AskCipherBase } from "@/components/panels/AskCipher";
import { Trident as TridentBase } from "@/components/panels/Trident";
import { ChartSaves as ChartSavesBase } from "@/components/panels/ChartSaves";
import { SetupScanner as SetupScannerBase } from "@/components/panels/SetupScanner";
import { Backtest as BacktestBase } from "@/components/panels/Backtest";
import { StrategyCatalogPanel as StrategyCatalogBase } from "@/components/panels/StrategyCatalog";
import { OptionsBacktest as OptionsBacktestBase } from "@/components/panels/OptionsBacktest";
import { GexReplay as GexReplayBase } from "@/components/panels/GexReplay";
import { Alerts as AlertsBase } from "@/components/panels/Alerts";
import { Settings as SettingsBase } from "@/components/panels/Settings";
import { MorningBrief as MorningBriefBase } from "@/components/panels/MorningBrief";
import { PaperPortfolios as PaperPortfoliosBase } from "@/components/panels/PaperPortfolios";
import { OptionsTerminal as OptionsTerminalBase } from "@/components/panels/OptionsTerminal";
import { PortfolioRisk as PortfolioRiskBase } from "@/components/panels/PortfolioRisk";
import { ChartWorkbench as ChartWorkbenchBase } from "@/components/panels/ChartWorkbench";
import { TraderJournal as TraderJournalBase } from "@/components/panels/TraderJournal";
import { CompanyContext as CompanyContextBase } from "@/components/panels/CompanyContext";
import { OperatorStatus as OperatorStatusBase } from "@/components/panels/OperatorStatus";
import { ResearchDesk as ResearchDeskBase } from "@/components/panels/ResearchDesk";
import { TickerWorkbench as TickerWorkbenchBase } from "@/components/panels/TickerWorkbench";

/**
 * One place that maps a sidebar label to the panel it renders.
 *
 * This exists because there are now two things that need to render "whatever panel
 * the label names": the classic single-panel view in `page.tsx`, and each tile in
 * Workspace mode's dockview grid. Two copies of the same 13-branch chain would drift
 * the first time a panel is added — the label→panel mapping lives here instead, and
 * both callers go through `<PanelHost/>`.
 *
 * Every panel below does its own polling (quotes, matrices, flow) independently of
 * page-level state. Without memoization, Header's 10s quote poll — or any other state
 * change in the page — would re-render whichever heavy panel (hundreds of heatmap
 * cells) happens to be mounted, even though its own props never changed. Wrapping at
 * the import site (rather than inside each panel file) keeps this a single-file,
 * low-risk fix.
 */
const StrikeMatrix = memo(StrikeMatrixBase);
const NightVision = memo(NightVisionBase);
const Spyglass = memo(SpyglassBase);
const News = memo(NewsBase);
const Watchlists = memo(WatchlistsBase);
const Standing = memo(StandingBase);
const Beliefs = memo(BeliefsBase);
const Holdings = memo(HoldingsBase);
const AskCipher = memo(AskCipherBase);
const Backtest = memo(BacktestBase);
const StrategyCatalog = memo(StrategyCatalogBase);
const OptionsBacktest = memo(OptionsBacktestBase);
const GexReplay = memo(GexReplayBase);
const Alerts = memo(AlertsBase);
const Trident = memo(TridentBase);
const ChartSaves = memo(ChartSavesBase);
const SetupScanner = memo(SetupScannerBase);
const Settings = memo(SettingsBase);
const MorningBrief = memo(MorningBriefBase);
const PaperPortfolios = memo(PaperPortfoliosBase);
const OptionsTerminal = memo(OptionsTerminalBase);
const PortfolioRisk = memo(PortfolioRiskBase);
const ChartWorkbench = memo(ChartWorkbenchBase);
const TraderJournal = memo(TraderJournalBase);
const CompanyContext = memo(CompanyContextBase);
const OperatorStatus = memo(OperatorStatusBase);
const ResearchDesk = memo(ResearchDeskBase);
const TickerWorkbench = memo(TickerWorkbenchBase);

/** Uppercase display titles — used by Header's subtitle and by dockview tab labels. */
export const PANEL_TITLES: Record<string, string> = {
  "Morning Brief": "MORNING BRIEF",
  "Research Desk": "RESEARCH DESK",
  "Ticker Workbench": "TICKER WORKBENCH",
  "Strike Matrix": "STRIKE MATRIX",
  "Options Terminal": "OPTIONS TERMINAL",
  "Chart Workbench": "CHART WORKBENCH",
  "Night Vision": "NIGHT VISION",
  Spyglass: "SPYGLASS",
  News: "NEWS",
  "My Watchlists": "MY WATCHLISTS",
  Standing: "STANDING",
  Beliefs: "BELIEFS",
  Holdings: "HOLDINGS",
  "Portfolio Risk": "PORTFOLIO RISK",
  "Trader Journal": "TRADER JOURNAL",
  "Company Context": "COMPANY & EVENT CONTEXT",
  "Paper Portfolios": "PAPER PORTFOLIOS",
  "Ask Cipher": "ASK CIPHER",
  Trident: "TRIDENT",
  "Chart Saves": "CHART SAVES",
  "Setup Scanner": "SETUP SCANNER",
  Backtest: "SIGNAL BACKTEST",
  "Options Backtest": "OPTIONS BACKTEST",
  "GEX Replay": "GEX REPLAY",
  Alerts: "LOCAL ALERTS",
  Strategies: "STRATEGY CATALOG",
  Settings: "SETTINGS",
  "Operator Status": "OPERATOR STATUS",
};

export function panelTitle(label: string): string {
  return PANEL_TITLES[label] ?? label.toUpperCase();
}

type PanelHostProps = {
  /** Sidebar label of the panel to render, e.g. "Strike Matrix". */
  panel: string;
  ticker: string;
  /**
   * Header toolbar portal target. Only the single-panel view has one — a tile in a
   * dockview grid must not portal its toolbar into the shared header (several tiles
   * would fight over the same node), so tiles pass nothing and StrikeMatrix /
   * NightVision / Trident fall back to rendering their toolbar inline, which they
   * already support.
   */
  toolbarSlot?: HTMLDivElement | null;
  /**
   * Controlled Spyglass sub-tab, for the single-panel view where the tabs are lifted
   * into Header's `rightSlot`. Omitted in tiles, where Spyglass renders its own tab bar.
   */
  spyglassTab?: SpyglassTab;
  onSpyglassTabChange?: (tab: SpyglassTab) => void;
  onNavigate?: (panel: string, ticker?: string) => void;
};

export function PanelHost({
  panel,
  ticker,
  toolbarSlot = null,
  spyglassTab,
  onSpyglassTabChange,
  onNavigate,
}: PanelHostProps) {
  switch (panel) {
    case "Morning Brief":
      return <MorningBrief ticker={ticker} onNavigate={onNavigate} />;
    case "Research Desk":
      return <ResearchDesk onNavigate={onNavigate} />;
    case "Ticker Workbench":
      return <TickerWorkbench ticker={ticker} onNavigate={onNavigate} />;
    case "Strike Matrix":
      return <StrikeMatrix ticker={ticker} toolbarSlot={toolbarSlot} />;
    case "Options Terminal":
      return <OptionsTerminal key={ticker} ticker={ticker} onNavigate={onNavigate} />;
    case "Chart Workbench":
      return <ChartWorkbench key={ticker} ticker={ticker} />;
    case "Night Vision":
      return <NightVision ticker={ticker} toolbarSlot={toolbarSlot} />;
    case "Spyglass":
      return (
        <Spyglass ticker={ticker} activeTab={spyglassTab} onActiveTabChange={onSpyglassTabChange} />
      );
    case "News":
      return <News ticker={ticker} />;
    case "My Watchlists":
      return <Watchlists />;
    case "Standing":
      return <Standing />;
    case "Beliefs":
      return <Beliefs />;
    case "Holdings":
      return <Holdings />;
    case "Portfolio Risk":
      return <PortfolioRisk />;
    case "Trader Journal":
      return <TraderJournal ticker={ticker} />;
    case "Company Context":
      return <CompanyContext key={ticker} ticker={ticker} />;
    case "Paper Portfolios":
      return <PaperPortfolios />;
    case "Ask Cipher":
      return <AskCipher ticker={ticker} />;
    case "Backtest":
      return <Backtest ticker={ticker} />;
    case "Strategies":
      return <StrategyCatalog />;
    case "Options Backtest":
      return <OptionsBacktest />;
    case "GEX Replay":
      return <GexReplay key={ticker} ticker={ticker} />;
    case "Alerts":
      return <Alerts ticker={ticker} />;
    case "Trident":
      return <Trident toolbarSlot={toolbarSlot} />;
    case "Chart Saves":
      return <ChartSaves />;
    case "Setup Scanner":
      return <SetupScanner onNavigate={onNavigate} />;
    case "Settings":
      return <Settings />;
    case "Operator Status":
      return <OperatorStatus />;
    default:
      // Reachable for real: a saved workspace layout can name a panel that a later
      // build renamed or removed. Say so plainly rather than rendering an empty tile.
      return (
        <div
          className="p-4 text-[12px]"
          style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}
        >
          No panel named &ldquo;{panel}&rdquo; in this build.
        </div>
      );
  }
}

export default PanelHost;
