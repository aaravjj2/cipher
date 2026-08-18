"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { MenuIcon, SearchIcon } from "@/components/icons";
import { addToWatchlist } from "@/lib/watchlist";
import { addWatchlistMember, createWatchlist, fetchProductStatus, fetchScanUniverse, fetchWatchlists, type ProductStatus } from "@/lib/api";
import { isSupabaseConfigured } from "@/lib/supabase";

type HeaderProps = {
  /** Current panel name, rendered uppercase in `.brand-sub` (e.g. "SETUP SCANNER"). */
  panelName: string;
  /** Panel-specific action buttons (Night Vision chart toggles, Spyglass links, etc). Built separately per panel. */
  rightSlot?: ReactNode;
  /**
   * Mobile hamburger click handler. The nav-toggle here is the SAME trigger as Sidebar's
   * mobile drawer — see the coordination note above the button below for why this is a
   * callback prop rather than owned state.
   */
  onMenuClick?: () => void;
  /** Active ticker + live quote, fetched by page.tsx from the real /api/quote endpoint. */
  ticker?: string;
  /** Pre-formatted price string, e.g. "$311.56". `null`/`undefined` renders a loading placeholder. */
  price?: string | null;
  /** Signed day-change percent. `null`/`undefined` renders a loading placeholder. */
  changePct?: number | null;
  /** Called with the trimmed, uppercased ticker when the user presses Enter in the search box. */
  onTickerSubmit?: (ticker: string) => void;
  /**
   * Workspace tabs (real site shows "1"/"2" next to the quote) — each slot remembers its
   * own active ticker; navigation/sidebar state stays shared across both. 1-based to match
   * the real site's on-screen labels.
   */
  workspaceCount?: number;
  activeWorkspace?: number;
  onWorkspaceChange?: (workspace: number) => void;
  /**
   * Portal target for the active panel's own toolbar (density/range/GEX-VEX pills, etc).
   * Confirmed against the real site: Strike Matrix / Night Vision / Trident render their
   * toolbar inline in this header row, not as a separate row below — unlike Spyglass's
   * Bio/Contract Search tabs (plain `rightSlot`), these toolbars have real internal state
   * (data-fetch params, refresh handlers) that must stay owned by the panel itself, so
   * they're projected here via a DOM portal rather than lifted into page.tsx.
   */
  toolbarSlotRef?: (el: HTMLDivElement | null) => void;
  /** "Welcome {displayName}!" text */
  displayName?: string;
};

/**
 * The portal target for a panel's toolbar, with an edge cue when it overflows.
 *
 * The slot has always scrolled horizontally, which is correct -- Trident's toolbar is a
 * GEX/VEX pair plus a selector plus five toggles plus a label, and letting it grow would
 * push the row past its siblings. What it lacked was any sign that it had scrolled. At 1600px
 * the Strike Matrix toolbar clips mid-control, so "Auto refresh" rendered as a truncated "Au"
 * against the panel edge and looked like a rendering fault rather than more content.
 *
 * A fade on whichever side has hidden content is the conventional cue and costs no layout
 * space, which matters here because the row is already full. Wrapping to a second row was the
 * alternative and would shift the grid down on every panel that has a long toolbar.
 */
function ToolbarSlot({ slotRef }: { slotRef: (el: HTMLDivElement | null) => void }) {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  useEffect(() => {
    if (!node) return;
    const measure = () => {
      const overflow = node.scrollWidth - node.clientWidth;
      // 2px of slack: sub-pixel layout rounding otherwise reports a permanent 1px overflow
      // and leaves the fade showing on a toolbar that fits.
      setEdges({
        start: node.scrollLeft > 2,
        end: overflow > 2 && node.scrollLeft < overflow - 2,
      });
    };
    measure();
    node.addEventListener("scroll", measure, { passive: true });
    // Toolbars are portalled in after mount and change with the active panel, so width has to
    // be observed rather than measured once.
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    for (const child of Array.from(node.children)) observer.observe(child);
    const mutations = new MutationObserver(() => {
      measure();
      for (const child of Array.from(node.children)) observer.observe(child);
    });
    mutations.observe(node, { childList: true, subtree: true });
    return () => {
      node.removeEventListener("scroll", measure);
      observer.disconnect();
      mutations.disconnect();
    };
  }, [node]);

  return (
    <div className="relative flex-1 min-w-0">
      <div
        ref={(el) => {
          setNode(el);
          slotRef(el);
        }}
        className="flex flex-row items-center gap-2 overflow-x-auto min-w-0 cipher-no-scrollbar"
      />
      {edges.start && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-0 w-8"
          style={{ background: "linear-gradient(to right, var(--bg), transparent)" }}
        />
      )}
      {edges.end && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-0 w-8"
          style={{ background: "linear-gradient(to left, var(--bg), transparent)" }}
        />
      )}
    </div>
  );
}

export function Header({
  panelName,
  rightSlot,
  onMenuClick,
  ticker = "AAPL",
  price,
  changePct,
  onTickerSubmit,
  toolbarSlotRef,
  workspaceCount = 2,
  activeWorkspace = 1,
  onWorkspaceChange,
  displayName = "Trader",
}: HeaderProps) {
  const [searchFocused, setSearchFocused] = useState(false);
  const [inputValue, setInputValue] = useState(ticker);
  const [watchlistAdded, setWatchlistAdded] = useState(false);
  const [watchlistBusy, setWatchlistBusy] = useState(false);
  const [productStatus, setProductStatus] = useState<ProductStatus | null>(null);
  const isPositive = (changePct ?? 0) >= 0;

  // Ticker suggestions come from the scanner's own optionable universe (580 names
  // from /api/scan/universe), not a hardcoded list — so the dropdown can only ever
  // offer symbols the rest of the app can actually load.
  const [universe, setUniverse] = useState<string[]>([]);
  const [highlight, setHighlight] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchScanUniverse(ctrl.signal)
      .then((res) => setUniverse(res.tickers ?? []))
      // A failed universe fetch just means no suggestions; typing still works.
      .catch(() => {});
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    const load = () => fetchProductStatus(ticker, ctrl.signal).then(setProductStatus).catch(() => {
      if (!ctrl.signal.aborted) setProductStatus(null);
    });
    void load();
    const id = setInterval(load, 60_000);
    return () => { ctrl.abort(); clearInterval(id); };
  }, [ticker]);

  const query = inputValue.trim().toUpperCase();
  const suggestions = useMemo(() => {
    if (!universe.length) return [];
    // An exact match alone is not worth a dropdown — it is what the box already says.
    if (!query) return universe.slice(0, 8);
    const prefix: string[] = [];
    const contains: string[] = [];
    for (const t of universe) {
      if (t === query) continue;
      if (t.startsWith(query)) prefix.push(t);
      else if (t.includes(query)) contains.push(t);
      if (prefix.length >= 8) break;
    }
    // Universe order is the backend's liquidity ranking, so it is preserved within
    // each group rather than re-sorted alphabetically.
    return [...prefix, ...contains].slice(0, 8);
  }, [universe, query]);

  useEffect(() => {
    setHighlight(0);
  }, [query]);

  const commit = (next: string) => {
    const symbol = next.trim().toUpperCase();
    if (!symbol) return;
    setInputValue(symbol);
    setSearchFocused(false);
    onTickerSubmit?.(symbol);
  };

  useEffect(() => {
    if (!watchlistAdded) return;
    const id = setTimeout(() => setWatchlistAdded(false), 1400);
    return () => clearTimeout(id);
  }, [watchlistAdded]);

  const addCurrentTickerToWatchlist = async () => {
    if (watchlistBusy) return;
    setWatchlistBusy(true);
    try {
      if (!isSupabaseConfigured()) {
        addToWatchlist(ticker);
      } else {
        const payload = await fetchWatchlists();
        const list = payload.watchlists[0] ?? await createWatchlist("Default");
        if (!list.tickers.includes(ticker)) await addWatchlistMember(list.id, ticker);
      }
      setWatchlistAdded(true);
    } catch {
      // The watchlist panel surfaces the detailed API error; the header remains a
      // non-blocking shortcut and must not claim a write that failed.
    } finally {
      setWatchlistBusy(false);
    }
  };

  // Keep the input in sync when the active ticker changes externally (e.g. a watchlist click).
  useEffect(() => {
    setInputValue(ticker);
  }, [ticker]);

  return (
    <header
      className="topbar flex flex-col lg:flex-row lg:items-center gap-2 lg:gap-[14px] px-4 py-[10px] sticky top-0 z-30 text-[13px]"
      style={{
        background: "color-mix(in srgb, var(--panel) 85%, transparent)",
        borderBottom: "1px solid var(--line)",
        fontFamily: "var(--font-sans)",
      }}
    >
      {/*
        Mobile layout: the spec (Responsive Behavior) describes 2 stacked rows below `lg:`
        that collapse into a single row at `lg:`. Rather than relying on flex-wrap (whose
        break point depends on summed item widths and drifts as content changes), each row
        is an explicit group that uses `lg:contents` at the desktop breakpoint — the
        grouping <div> drops out of layout entirely at `lg:` so its children rejoin the
        header's own flex row directly, without an extra wrapping box affecting alignment.
      */}

      {/* Row 1 (mobile): nav-toggle + brand + search */}
      <div className="flex flex-row items-center gap-[14px] lg:contents">
        {/*
          Mobile nav-toggle: coordination note (judgment call, see task spec).
          Sidebar.tsx manages its own `mobileOpen` drawer state internally with no external
          hook by default, but now accepts optional `mobileOpen` / `onMobileOpenChange` props
          for controlled usage — when controlled, Sidebar suppresses its own built-in
          hamburger so there is exactly one trigger on screen. This Header never touches
          Sidebar's state directly (Header has no knowledge Sidebar exists); instead it
          exposes `onMenuClick`. The future parent (page.tsx, wired later) will own a single
          `mobileNavOpen` boolean and pass `onMenuClick={() => setMobileNavOpen(true)}` here
          and `mobileOpen={mobileNavOpen}` / `onMobileOpenChange={setMobileNavOpen}` to
          Sidebar — lifting the shared state up rather than having either component reach
          into the other.
        */}
        <button
          type="button"
          aria-label="Open navigation"
          onClick={onMenuClick}
          className="nav-toggle lg:hidden grid place-items-center w-6 h-6 shrink-0 rounded-[6px] bg-transparent"
          style={{ color: "var(--text)" }}
        >
          <MenuIcon width={24} height={24} />
        </button>

        {/*
          Brand: mark only. The "CIPHER / <PANEL>" text block held a fixed
          lg:w-[209px], and the panel toolbars that portal into this row
          (Strike Matrix's density + range + metric pills, Night Vision's overlay
          toggles) were overflowing as a result — "Matrix" rendered clipped to
          "Matri" behind a horizontal scrollbar. The wordmark was the least
          load-bearing thing competing for that space: the active panel is already
          named in the sidebar, highlighted, so the subtitle was a third copy of
          information the user can see twice over. Dropping it and the fixed width
          shifts the whole row left and gives the toolbar room to lay out.
          The full name stays as the mark's title for accessibility.
        */}
        <div className="brand flex flex-row items-center h-[26px] shrink-0">
          <span
            title={`Cipher — ${panelName}`}
            className="brand-mark block w-[26px] h-[26px] rounded-[7px] shrink-0 bg-no-repeat bg-cover bg-center"
            style={{
              backgroundImage: "url(/seo/cipher-logo.jpg)",
              boxShadow:
                "inset 0 0 0 1px rgba(255,255,255,0.06), 0 4px 14px color-mix(in srgb, var(--accent) 25%, transparent)",
            }}
          />
        </div>

        {/* Ticker search */}
        <div
          className="search relative flex flex-row items-center flex-1 min-w-[120px] lg:flex-none lg:w-[150px] h-[34.667px] px-[9px] rounded-[8px]"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
        >
          <SearchIcon width={14} height={14} className="shrink-0" style={{ color: "var(--text-mute)" }} />
          <input
            type="text"
            value={inputValue}
            placeholder="TICKER"
            onFocus={() => setSearchFocused(true)}
            // Delayed so a click on a suggestion lands before the list unmounts.
            onBlur={() => setTimeout(() => setSearchFocused(false), 120)}
            onChange={(e) => setInputValue(e.target.value)}
            role="combobox"
            aria-expanded={searchFocused && suggestions.length > 0}
            aria-controls="ticker-suggestions"
            aria-autocomplete="list"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" && suggestions.length) {
                e.preventDefault();
                setHighlight((h) => (h + 1) % suggestions.length);
                return;
              }
              if (e.key === "ArrowUp" && suggestions.length) {
                e.preventDefault();
                setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
                return;
              }
              if (e.key === "Escape") {
                setSearchFocused(false);
                return;
              }
              if (e.key !== "Enter") return;
              // Enter takes the highlighted suggestion when the list is open, so
              // arrow-then-Enter works; otherwise it submits exactly what was typed.
              const picked = searchFocused && suggestions[highlight];
              commit(picked || inputValue);
              e.currentTarget.blur();
            }}
            className="flex-1 min-w-0 p-2 bg-transparent border-none outline-none uppercase"
            style={{
              fontSize: "13px",
              fontWeight: 600,
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.78px",
              color: "var(--text)",
            }}
          />
          <kbd
            className="search-kbd shrink-0 rounded-[4px] px-[5px] py-[1px]"
            style={{
              fontSize: "10px",
              fontFamily: "var(--font-mono)",
              color: "var(--text-mute)",
              background: "var(--panel-2)",
              border: "1px solid var(--line)",
              borderBottom: "2px solid var(--line)",
            }}
          >
            /
          </kbd>

          {/* Suggestions: prefix matches first, then substring, capped at 8. */}
          {searchFocused && suggestions.length > 0 && (
            <div
              id="ticker-suggestions"
              role="listbox"
              className="suggestions absolute left-0 w-full overflow-hidden rounded-[8px]"
              style={{
                top: "calc(100% + 6px)",
                zIndex: 50,
                background: "var(--panel-2)",
                border: "1px solid var(--line)",
                boxShadow: "0 14px 38px rgba(0,0,0,0.6)",
              }}
            >
              {suggestions.map((symbol, i) => (
                <button
                  key={symbol}
                  type="button"
                  role="option"
                  aria-selected={i === highlight}
                  // onMouseDown, not onClick: the input's blur fires first and would
                  // otherwise tear the list down before a click could register.
                  onMouseDown={(e) => {
                    e.preventDefault();
                    commit(symbol);
                  }}
                  onMouseEnter={() => setHighlight(i)}
                  className="block w-full px-[10px] py-[6px] text-left uppercase"
                  style={{
                    fontSize: "12px",
                    fontWeight: 600,
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.6px",
                    background: i === highlight ? "var(--nav-active)" : "transparent",
                    color: i === highlight ? "var(--text)" : "var(--text-dim)",
                  }}
                >
                  {symbol}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Row 2 (mobile): quote + rightSlot + welcome/logout */}
      <div className="flex flex-row items-center gap-[10px] lg:contents">
        {/* Quote */}
        <div
          className="quote flex flex-row items-baseline gap-[9px] lg:w-[173px] h-[28.667px] px-3 py-[5px] rounded-[8px] shrink-0"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
        >
          <span
            className="quote-ticker"
            style={{
              fontSize: "13px",
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              letterSpacing: "1.04px",
              color: "var(--text)",
            }}
          >
            {ticker}
          </span>
          <span className="quote-price" style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
            {price ?? "···"}
          </span>
          <span
            className="quote-change"
            style={{
              fontFamily: "var(--font-mono)",
              // Cipher uses purple=up / red=down, NOT the conventional green=up.
              color: changePct == null ? "var(--text-mute)" : isPositive ? "var(--accent)" : "var(--neg)",
            }}
          >
            {changePct == null ? "···" : `${isPositive ? "+" : ""}${changePct.toFixed(2)}%`}
          </span>
        </div>

        {/* Workspace tabs — each slot remembers its own active ticker */}
        {onWorkspaceChange && (
          <div
            className="flex flex-row items-center gap-[2px] rounded-[8px] p-[2px] shrink-0"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
          >
            {Array.from({ length: workspaceCount }, (_, i) => i + 1).map((n) => {
              const active = n === activeWorkspace;
              return (
                <button
                  key={n}
                  type="button"
                  onClick={() => onWorkspaceChange(n)}
                  aria-pressed={active}
                  aria-label={`Workspace ${n}`}
                  className="grid place-items-center w-[26px] h-[26px] rounded-[6px] text-[12px] font-bold"
                  style={{
                    background: active ? "var(--nav-active)" : "transparent",
                    color: active ? "var(--text)" : "var(--text-dim)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {n}
                </button>
              );
            })}
          </div>
        )}

        {/* Quick-add current ticker to the authenticated hosted watchlist, or local fallback. */}
        <button
          type="button"
          onClick={() => void addCurrentTickerToWatchlist()}
          className="shrink-0 rounded-[8px] px-[12px] py-2 text-[12px] font-semibold whitespace-nowrap"
          style={{
            border: `1px solid ${watchlistAdded ? "var(--accent)" : "var(--line)"}`,
            color: watchlistAdded ? "var(--accent)" : "var(--text-dim)",
          }}
        >
          {watchlistBusy ? "Saving…" : watchlistAdded ? "Added" : "+ Watchlist"}
        </button>

        {/* Panel's own toolbar (Strike Matrix / Night Vision / Trident) portals into this slot.
            Unlike rightSlot below (a couple of tabs, safe to let overflow visibly), this can
            hold many buttons (Trident's GEX/VEX + selector + 5 toggles + label), so it always
            scrolls internally — flex-1 min-w-0 is what lets overflow-x-auto actually engage
            instead of the row just growing past its siblings. */}
        {toolbarSlotRef && <ToolbarSlot slotRef={toolbarSlotRef} />}

        {/* Panel-specific action buttons */}
        {rightSlot && (
          <div className="flex flex-row items-center gap-2 overflow-x-auto lg:overflow-visible">
            {rightSlot}
          </div>
        )}

        {/*
          Welcome text + logout pinned to the far right on desktop via ml-auto on the welcome span.
          The greeting is decoration and it competes with the panel toolbar for the same row: it
          costs 124px plus its gap, and the Strike Matrix toolbar — the widest of the panels —
          needs 818px but was only handed 698px, so "Auto refresh" clipped mid-word. Reclaiming the
          greeting's ~138px yields 836px, which clears 818 with a little slack. The 1850px
          threshold is that shortfall added back to the 1707px viewport it was measured at, so the
          greeting reappears only once the toolbar is already satisfied. `ml-auto` moves to the
          badge below it so the right edge still pins.
        */}
        <span className="hidden min-[1850px]:inline min-[1850px]:ml-auto" style={{ color: "var(--text-mute)" }}>
          Welcome {displayName}! 🚀
        </span>
        {/*
          This slot held a "Logout" button with no onClick, cloned from the reference site.
          There is no authentication in this app at all — app/server.mjs has no session,
          cookie or login path, and its only credential check guards /api/scanner-ingest.
          A logout control therefore implied an account boundary that does not exist, which
          is the wrong thing to imply about a service reachable over a network.

          What replaces it is the one fact about this build worth pinning to the header:
          Cipher never places an order. That is an enforced invariant, not a hopeful label
          — core/research_platform/seven_layer_stack.py fails the boundary audit if any
          order-submitting symbol appears in the tree.
        */}
        <span
          className="shrink-0 rounded-[8px] px-[10px] py-2 text-[10px] font-bold uppercase"
          style={{ border: "1px solid var(--line)", color: productStatus?.healthy ? "var(--positive)" : productStatus ? "var(--gold)" : "var(--text-mute)" }}
          title={productStatus ? productStatus.items.map((item) => `${item.name}: ${item.state} (${item.source})`).join("\n") : "Freshness status unavailable"}
        >
          {productStatus ? `${productStatus.session.phase} · ${productStatus.exceptions.length ? `${productStatus.exceptions.length} stale` : "fresh"}` : "data —"}
        </span>
        <span
          className="ml-auto min-[1850px]:ml-0 shrink-0 rounded-[8px] px-[14px] py-2 text-[11px] font-bold uppercase"
          style={{ border: "1px solid var(--line)", color: "var(--text-mute)", letterSpacing: "0.1em" }}
          title="Cipher is research-only: it reads market data and runs backtests, and has no order-placing path."
        >
          Research only
        </span>
      </div>
    </header>
  );
}

export default Header;
