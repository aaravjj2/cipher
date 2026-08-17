"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";
import { CommandPalette, useCommandPaletteShortcut } from "@/components/CommandPalette";
import { PanelHost, panelTitle } from "@/components/PanelHost";
import { TickerStrip } from "@/components/TickerStrip";
import { Workspace } from "@/components/panels/Workspace";
import { SpyglassHeaderTabs, type SpyglassTab } from "@/components/panels/Spyglass";
import { fetchQuote, type RealQuote } from "@/lib/api";

const QUOTE_REFRESH_MS = 10_000;

// Confirmed against the real site: the header subtitle changes with Spyglass's sub-view
// (e.g. "BIO FLOW" while on Bio), not just the sidebar panel name.
const SPYGLASS_SUB_TITLES: Record<SpyglassTab, string> = {
  spyglass: "SPYGLASS",
  bio: "BIO FLOW",
  contractSearch: "CONTRACT SEARCH",
};

const WORKSPACE_COUNT = 2;

export default function Home() {
  const [activePanel, setActivePanel] = useState("Morning Brief");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [spyglassTab, setSpyglassTab] = useState<SpyglassTab>("spyglass");
  /**
   * Workspace (multi-pane) mode is opt-in and off by default: single-panel mode is still
   * the primary way the app is used, and this only changes how panels are laid out — the
   * panels themselves are identical components either way (see components/PanelHost.tsx).
   */
  const [tiledMode, setTiledMode] = useState(false);
  /**
   * Tile-open requests for Workspace mode. The counter matters: after closing a tile,
   * clicking the same sidebar entry again produces an unchanged label, so only a bumped
   * `seq` re-opens it.
   */
  const [openRequest, setOpenRequest] = useState({ label: "Strike Matrix", seq: 0 });
  const [paletteOpen, setPaletteOpen] = useState(false);
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
    const controller = new AbortController();
    void Promise.resolve().then(() => loadQuote(controller.signal));
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
    setOpenRequest((prev) => ({ label: panel, seq: prev.seq + 1 }));
  }, []);
  const handleMobileOpenChange = useCallback((open: boolean) => setMobileNavOpen(open), []);
  const handleMenuClick = useCallback(() => setMobileNavOpen(true), []);
  const handlePaletteOpen = useCallback(() => setPaletteOpen(true), []);
  const handlePanelNavigate = useCallback((panel: string, nextTicker?: string) => {
    if (nextTicker) setTicker(nextTicker);
    handleActivePanelChange(panel);
  }, [handleActivePanelChange, setTicker]);

  useCommandPaletteShortcut(setPaletteOpen);

  const rightSlot = useMemo(
    () =>
      !tiledMode && activePanel === "Spyglass" ? (
        <SpyglassHeaderTabs activeTab={spyglassTab} onChange={setSpyglassTab} />
      ) : undefined,
    [tiledMode, activePanel, spyglassTab]
  );

  const price = quote ? `$${quote.price_context.toFixed(2)}` : undefined;
  const changePct = quote ? quote.day_change_pct : undefined;

  const headerPanelName = tiledMode
    ? "WORKSPACE"
    : activePanel === "Spyglass"
      ? SPYGLASS_SUB_TITLES[spyglassTab]
      : panelTitle(activePanel);

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--bg)" }}>
      <Sidebar
        activePanel={activePanel}
        onActivePanelChange={handleActivePanelChange}
        mobileOpen={mobileNavOpen}
        onMobileOpenChange={handleMobileOpenChange}
        tiledMode={tiledMode}
        onTiledModeChange={setTiledMode}
        onCommandPaletteOpen={handlePaletteOpen}
      />

      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        <Header
          panelName={headerPanelName}
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

        <TickerStrip activeTicker={ticker} onSelect={setTicker} />

        {tiledMode ? (
          // Workspace mode owns its own scrolling: the grid must fill the viewport exactly
          // (dockview sizes itself from its container), and each tile scrolls internally.
          <main className="flex-1 min-h-0 overflow-hidden">
            <Workspace ticker={ticker} openRequest={openRequest} />
          </main>
        ) : (
          <main className="flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-6">
            <PanelHost
              panel={activePanel}
              ticker={ticker}
              toolbarSlot={toolbarSlot}
              spyglassTab={spyglassTab}
              onSpyglassTabChange={setSpyglassTab}
              onNavigate={handlePanelNavigate}
            />
          </main>
        )}
      </div>

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onPanelSelect={handleActivePanelChange}
        onTickerSelect={setTicker}
      />
    </div>
  );
}
