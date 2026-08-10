"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { Command } from "cmdk";
import { NAV_SECTIONS } from "@/components/Sidebar";
import { fetchScanUniverse } from "@/lib/api";

/**
 * Global command palette (Ctrl/Cmd+K) — jump to any panel, or switch the active ticker,
 * without reaching for the sidebar or the search box.
 *
 * Built on `cmdk`, which is the same primitive shadcn/ui's own `Command` component wraps,
 * so this matches the design system already in use rather than introducing a second one.
 *
 * Panels come from `Sidebar`'s own `NAV_SECTIONS`, not a parallel list, so the palette
 * can't drift out of sync with the nav. Tickers come from the scanner's optionable
 * universe (`/api/scan/universe`) — the same source as Header's suggestions — so the
 * palette can only offer symbols the rest of the app can actually load.
 */

const MAX_TICKER_RESULTS = 8;

/** Same prefix-then-substring ranking Header's ticker box uses, so both feel identical. */
function rankTickers(universe: string[], query: string): string[] {
  if (!universe.length) return [];
  if (!query) return universe.slice(0, MAX_TICKER_RESULTS);
  const prefix: string[] = [];
  const contains: string[] = [];
  for (const t of universe) {
    if (t.startsWith(query)) prefix.push(t);
    else if (t.includes(query)) contains.push(t);
    if (prefix.length >= MAX_TICKER_RESULTS) break;
  }
  // Universe order is the backend's liquidity ranking, so it is preserved within each
  // group rather than re-sorted alphabetically.
  return [...prefix, ...contains].slice(0, MAX_TICKER_RESULTS);
}

type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPanelSelect: (label: string) => void;
  onTickerSelect: (ticker: string) => void;
};

export function CommandPalette({
  open,
  onOpenChange,
  onPanelSelect,
  onTickerSelect,
}: CommandPaletteProps) {
  const [universe, setUniverse] = useState<string[]>([]);
  // Fetched on first open rather than on mount: the palette may never be opened, and the
  // universe is a 580-symbol payload. Held here rather than in the body below so it
  // survives the body unmounting on close.
  const requested = useRef(false);

  useEffect(() => {
    if (!open || requested.current) return;
    requested.current = true;
    const ctrl = new AbortController();
    fetchScanUniverse(ctrl.signal)
      .then((res) => setUniverse(res.tickers ?? []))
      // A failed universe fetch just means no ticker rows; panel jumps still work.
      .catch(() => {});
    return () => ctrl.abort();
  }, [open]);

  if (!open) return null;
  return (
    <PaletteBody
      universe={universe}
      onOpenChange={onOpenChange}
      onPanelSelect={onPanelSelect}
      onTickerSelect={onTickerSelect}
    />
  );
}

/**
 * The palette itself, mounted only while open. Unmounting on close is what resets the
 * query — no effect needs to clear it, which also means no state is written during an
 * effect just to undo the previous session's typing.
 */
function PaletteBody({
  universe,
  onOpenChange,
  onPanelSelect,
  onTickerSelect,
}: Omit<CommandPaletteProps, "open"> & { universe: string[] }) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toUpperCase();

  const panelMatches = useMemo(() => {
    const rows = NAV_SECTIONS.flatMap((section) =>
      section.items.map((item) => ({ label: item.label, section: section.label }))
    );
    if (!normalized) return rows;
    return rows.filter((row) => row.label.toUpperCase().includes(normalized));
  }, [normalized]);

  const tickerMatches = useMemo(() => rankTickers(universe, normalized), [universe, normalized]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center px-4 pt-[12vh]"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onMouseDown={(e) => {
        // Backdrop only — a mousedown that started inside the panel must not close it.
        if (e.target === e.currentTarget) onOpenChange(false);
      }}
    >
      {/* shouldFilter={false}: filtering is done above so the ticker group can be capped at
          8 of 580 symbols and ranked the same way Header ranks them, rather than rendering
          the whole universe and letting cmdk score it. cmdk still owns keyboard
          navigation, selection and the roving-focus semantics. */}
      <Command
        label="Command palette"
        shouldFilter={false}
        loop
        className="w-full max-w-[560px] overflow-hidden rounded-[10px]"
        style={{
          background: "var(--panel)",
          border: "1px solid var(--line)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.65)",
          fontFamily: "var(--font-mono)",
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onOpenChange(false);
          }
        }}
      >
        <Command.Input
          autoFocus
          value={query}
          onValueChange={setQuery}
          placeholder="Jump to a panel or ticker…"
          className="w-full bg-transparent px-4 py-3 text-[13px] outline-none"
          style={{ borderBottom: "1px solid var(--line)", color: "var(--text)" }}
        />
        <Command.List className="max-h-[52vh] overflow-y-auto p-2">
          {panelMatches.length === 0 && tickerMatches.length === 0 && (
            <div className="px-3 py-6 text-center text-[12px]" style={{ color: "var(--text-mute)" }}>
              Nothing matches “{query}”.
            </div>
          )}

          {panelMatches.length > 0 && (
            <Command.Group heading="PANELS">
              {panelMatches.map((row) => (
                <Command.Item
                  key={row.label}
                  value={`panel:${row.label}`}
                  onSelect={() => {
                    onPanelSelect(row.label);
                    onOpenChange(false);
                  }}
                  className="flex cursor-pointer flex-row items-center justify-between rounded-[7px] px-3 py-[7px] text-[12.5px]"
                  style={{ color: "var(--text-dim)" }}
                >
                  <span>{row.label}</span>
                  <span className="text-[10px]" style={{ color: "var(--text-mute)" }}>
                    {row.section}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}

          {tickerMatches.length > 0 && (
            <Command.Group heading="TICKERS">
              {tickerMatches.map((symbol) => (
                <Command.Item
                  key={symbol}
                  value={`ticker:${symbol}`}
                  onSelect={() => {
                    onTickerSelect(symbol);
                    onOpenChange(false);
                  }}
                  className="flex cursor-pointer flex-row items-center justify-between rounded-[7px] px-3 py-[7px] text-[12.5px]"
                  style={{ color: "var(--text-dim)" }}
                >
                  <span style={{ letterSpacing: "0.06em" }}>{symbol}</span>
                  <span className="text-[10px]" style={{ color: "var(--text-mute)" }}>
                    set active ticker
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
        </Command.List>
      </Command>
    </div>
  );
}

/** Registers the Ctrl/Cmd+K toggle. Kept next to the palette so a caller only has to
 *  hold the boolean, not know the shortcut. */
export function useCommandPaletteShortcut(setOpen: Dispatch<SetStateAction<boolean>>) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "k" || !(e.metaKey || e.ctrlKey)) return;
      e.preventDefault();
      setOpen((prev) => !prev);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [setOpen]);
}

export default CommandPalette;
