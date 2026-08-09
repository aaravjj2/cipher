"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";
import { StrikeMatrix as StrikeMatrixBase } from "@/components/panels/StrikeMatrix";
import { NightVision as NightVisionBase } from "@/components/panels/NightVision";
import { Spyglass as SpyglassBase, SpyglassHeaderTabs, type SpyglassTab } from "@/components/panels/Spyglass";
import { Watchlists as WatchlistsBase } from "@/components/panels/Watchlists";
import { Journal as JournalBase } from "@/components/panels/Journal";
import { Trident as TridentBase } from "@/components/panels/Trident";
import { ChartSaves as ChartSavesBase } from "@/components/panels/ChartSaves";
import { SetupScanner as SetupScannerBase } from "@/components/panels/SetupScanner";
import { Backtest as BacktestBase } from "@/components/panels/Backtest";
import { StrategyCatalogPanel as StrategyCatalogBase } from "@/components/panels/StrategyCatalog";
import { Settings as SettingsBase } from "@/components/panels/Settings";
import { fetchQuote, type RealQuote } from "@/lib/api";

/**
 * Every panel below does its own polling (quotes, matrices, flow) independently of
 * page-level state. Without memoization, Header's 10s quote poll — or any other state
 * change here — would re-render whichever heavy panel (hundreds of heatmap cells) happens
 * to be mounted, even though its own props never changed. Wrapping at the import site
 * (rather than inside each panel file) keeps this a single-file, low-risk fix.
 */
const StrikeMatrix = memo(StrikeMatrixBase);
const NightVision = memo(NightVisionBase);
const Spyglass = memo(SpyglassBase);
const Watchlists = memo(WatchlistsBase);
const Journal = memo(JournalBase);
const Backtest = memo(BacktestBase);
const StrategyCatalog = memo(StrategyCatalogBase);
const Trident = memo(TridentBase);
const ChartSaves = memo(ChartSavesBase);
const SetupScanner = memo(SetupScannerBase);
const Settings = memo(SettingsBase);

const QUOTE_REFRESH_MS = 10_000;

const PANEL_TITLES: Record<string, string> = {
  "Strike Matrix": "STRIKE MATRIX",
  "Night Vision": "NIGHT VISION",
  Spyglass: "SPYGLASS",
  "My Watchlists": "MY WATCHLISTS",
  Journal: "TRADING JOURNAL",
  Trident: "TRIDENT",
  "Chart Saves": "CHART SAVES",
  "Setup Scanner": "SETUP SCANNER",
  Backtest: "SIGNAL BACKTEST",
  Strategies: "STRATEGY CATALOG",
  Settings: "SETTINGS",
};

// Confirmed against the real site: the header subtitle changes with Spyglass's sub-view
// (e.g. "BIO FLOW" while on Bio), not just the sidebar panel name.
const SPYGLASS_SUB_TITLES: Record<SpyglassTab, string> = {
  spyglass: "SPYGLASS",
  bio: "BIO FLOW",
  contractSearch: "CONTRACT SEARCH",
};

const WORKSPACE_COUNT = 2;

export default function Home() {
  const [activePanel, setActivePanel] = useState("Strike Matrix");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [spyglassTab, setSpyglassTab] = useState<SpyglassTab>("spyglass");
  // Workspace tabs (real site's "1"/"2" next to the quote) — each slot keeps its own
  // active ticker; navigation/sidebar state stays shared across both, per the header
  // layout (tabs sit next to the ticker search, not the sidebar).
  const [workspaceTickers, setWorkspaceTickers] = useState<string[]>(() => Array(WORKSPACE_COUNT).fill("AAPL"));
  const [activeWorkspace, setActiveWorkspace] = useState(1);
  const ticker = workspaceTickers[activeWorkspace - 1];
  const setTicker = useCallback((next: string) => {
    setWorkspaceTickers((prev) => prev.map((t, i) => (i === activeWorkspace - 1 ? next : t)));
  }, [activeWorkspace]);
  const [quote, setQuote] = useState<RealQuote | null>(null);
  const [toolbarSlot, setToolbarSlot] = useState<HTMLDivElement | null>(null);

  const loadQuote = useCallback(async (signal?: AbortSignal) => {
    try {
      const q = await fetchQuote(ticker, signal);
      setQuote(q);
    } catch {
      if (!signal?.aborted) setQuote(null);
    }
  }, [ticker]);

  useEffect(() => {
    setQuote(null);
    const controller = new AbortController();
    loadQuote(controller.signal);
    const interval = setInterval(() => loadQuote(), QUOTE_REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [loadQuote]);

  // Stable callback identities so Sidebar/Header (and, by extension, memoized panels)
  // don't see a "changed" prop on every quote-poll re-render of Home.
  const handleActivePanelChange = useCallback((panel: string) => {
    setActivePanel(panel);
    if (panel === "Spyglass") setSpyglassTab("spyglass");
  }, []);
  const handleMobileOpenChange = useCallback((open: boolean) => setMobileNavOpen(open), []);
  const handleMenuClick = useCallback(() => setMobileNavOpen(true), []);

  const rightSlot = useMemo(
    () =>
      activePanel === "Spyglass" ? (
        <SpyglassHeaderTabs activeTab={spyglassTab} onChange={setSpyglassTab} />
      ) : undefined,
    [activePanel, spyglassTab]
  );

  const price = quote ? `$${quote.price_context.toFixed(2)}` : undefined;
  const changePct = quote ? quote.day_change_pct : undefined;

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--bg)" }}>
      <Sidebar
        activePanel={activePanel}
        onActivePanelChange={handleActivePanelChange}
        mobileOpen={mobileNavOpen}
        onMobileOpenChange={handleMobileOpenChange}
      />

      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        <Header
          panelName={
            activePanel === "Spyglass"
              ? SPYGLASS_SUB_TITLES[spyglassTab]
              : PANEL_TITLES[activePanel] ?? activePanel.toUpperCase()
          }
          rightSlot={rightSlot}
          onMenuClick={handleMenuClick}
          ticker={ticker}
          onTickerSubmit={setTicker}
          price={price}
          changePct={changePct}
          toolbarSlotRef={setToolbarSlot}
          workspaceCount={WORKSPACE_COUNT}
          activeWorkspace={activeWorkspace}
          onWorkspaceChange={setActiveWorkspace}
        />

        <main className="flex-1 overflow-y-auto overflow-x-hidden p-6">
          {activePanel === "Strike Matrix" && <StrikeMatrix ticker={ticker} toolbarSlot={toolbarSlot} />}
          {activePanel === "Night Vision" && <NightVision ticker={ticker} toolbarSlot={toolbarSlot} />}
          {activePanel === "Spyglass" && (
            <Spyglass ticker={ticker} activeTab={spyglassTab} onActiveTabChange={setSpyglassTab} />
          )}
          {activePanel === "My Watchlists" && <Watchlists />}
          {activePanel === "Journal" && <Journal />}
          {activePanel === "Backtest" && <Backtest ticker={ticker} />}
          {activePanel === "Strategies" && <StrategyCatalog />}
          {activePanel === "Trident" && <Trident toolbarSlot={toolbarSlot} />}
          {activePanel === "Chart Saves" && <ChartSaves />}
          {activePanel === "Setup Scanner" && <SetupScanner />}
          {activePanel === "Settings" && <Settings />}
        </main>
      </div>
    </div>
  );
}
