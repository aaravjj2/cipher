"use client";

import { memo } from "react";
import { StrikeMatrix as StrikeMatrixBase } from "@/components/panels/StrikeMatrix";
import { NightVision as NightVisionBase } from "@/components/panels/NightVision";
import { Spyglass as SpyglassBase, type SpyglassTab } from "@/components/panels/Spyglass";
import { Watchlists as WatchlistsBase } from "@/components/panels/Watchlists";
import { Standing as StandingBase } from "@/components/panels/Standing";
import { Holdings as HoldingsBase } from "@/components/panels/Holdings";
import { AskCipher as AskCipherBase } from "@/components/panels/AskCipher";
import { Trident as TridentBase } from "@/components/panels/Trident";
import { ChartSaves as ChartSavesBase } from "@/components/panels/ChartSaves";
import { SetupScanner as SetupScannerBase } from "@/components/panels/SetupScanner";
import { Backtest as BacktestBase } from "@/components/panels/Backtest";
import { StrategyCatalogPanel as StrategyCatalogBase } from "@/components/panels/StrategyCatalog";
import { Settings as SettingsBase } from "@/components/panels/Settings";

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
const Watchlists = memo(WatchlistsBase);
const Standing = memo(StandingBase);
const Holdings = memo(HoldingsBase);
const AskCipher = memo(AskCipherBase);
const Backtest = memo(BacktestBase);
const StrategyCatalog = memo(StrategyCatalogBase);
const Trident = memo(TridentBase);
const ChartSaves = memo(ChartSavesBase);
const SetupScanner = memo(SetupScannerBase);
const Settings = memo(SettingsBase);

/** Uppercase display titles — used by Header's subtitle and by dockview tab labels. */
export const PANEL_TITLES: Record<string, string> = {
  "Strike Matrix": "STRIKE MATRIX",
  "Night Vision": "NIGHT VISION",
  Spyglass: "SPYGLASS",
  "My Watchlists": "MY WATCHLISTS",
  Standing: "STANDING",
  Holdings: "HOLDINGS",
  "Ask Cipher": "ASK CIPHER",
  Trident: "TRIDENT",
  "Chart Saves": "CHART SAVES",
  "Setup Scanner": "SETUP SCANNER",
  Backtest: "SIGNAL BACKTEST",
  Strategies: "STRATEGY CATALOG",
  Settings: "SETTINGS",
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
};

export function PanelHost({
  panel,
  ticker,
  toolbarSlot = null,
  spyglassTab,
  onSpyglassTabChange,
}: PanelHostProps) {
  switch (panel) {
    case "Strike Matrix":
      return <StrikeMatrix ticker={ticker} toolbarSlot={toolbarSlot} />;
    case "Night Vision":
      return <NightVision ticker={ticker} toolbarSlot={toolbarSlot} />;
    case "Spyglass":
      return (
        <Spyglass ticker={ticker} activeTab={spyglassTab} onActiveTabChange={onSpyglassTabChange} />
      );
    case "My Watchlists":
      return <Watchlists />;
    case "Standing":
      return <Standing />;
    case "Holdings":
      return <Holdings />;
    case "Ask Cipher":
      return <AskCipher />;
    case "Backtest":
      return <Backtest ticker={ticker} />;
    case "Strategies":
      return <StrategyCatalog />;
    case "Trident":
      return <Trident toolbarSlot={toolbarSlot} />;
    case "Chart Saves":
      return <ChartSaves />;
    case "Setup Scanner":
      return <SetupScanner />;
    case "Settings":
      return <Settings />;
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
