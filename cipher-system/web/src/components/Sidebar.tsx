"use client";

import { useState, type ComponentType, type SVGProps } from "react";
import { cn } from "@/lib/utils";
import {
  ChevronLeftIcon,
  GridIcon,
  NightVisionIcon,
  SearchIcon,
  StarIcon,
  JournalIcon,
  PortfolioIcon,
  ChatIcon,
  TridentIcon,
  BookmarkIcon,
  ScannerIcon,
  SettingsIcon,
  MenuIcon,
  NewsIcon,
  OptionsBacktestIcon,
  ReplayIcon,
  AlertIcon,
} from "@/components/icons";

type IconComponent = ComponentType<SVGProps<SVGSVGElement>>;

export type NavItem = {
  label: string;
  icon: IconComponent;
};

export type NavSection = {
  label: string;
  items: NavItem[];
};

/** Exported so the command palette offers exactly the panels the sidebar does — one list,
 *  so a panel added here shows up in the palette without a second edit. */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: "TODAY",
    items: [
      { label: "Morning Brief", icon: GridIcon },
      { label: "Earnings Radar", icon: StarIcon },
      { label: "Research Desk", icon: SearchIcon },
    ],
  },
  {
    label: "DISCOVER",
    items: [
      { label: "Setup Scanner", icon: ScannerIcon },
      { label: "My Watchlists", icon: StarIcon },
      { label: "News", icon: NewsIcon },
    ],
  },
  {
    label: "ANALYZE",
    items: [
      { label: "Ticker Workbench", icon: GridIcon },
      { label: "Night Vision", icon: NightVisionIcon },
      { label: "Options Terminal", icon: OptionsBacktestIcon },
      { label: "Strike Matrix", icon: GridIcon },
      { label: "Spyglass", icon: SearchIcon },
      { label: "Company Context", icon: NewsIcon },
      { label: "Ask Cipher", icon: ChatIcon },
    ],
  },
  {
    label: "PLAN",
    items: [
      { label: "Chart Workbench", icon: JournalIcon },
      { label: "Portfolio Risk", icon: PortfolioIcon },
      { label: "Holdings", icon: PortfolioIcon },
      { label: "Alerts", icon: AlertIcon },
    ],
  },
  {
    label: "REVIEW",
    items: [
      { label: "Paper Portfolios", icon: PortfolioIcon },
      { label: "Trader Journal", icon: JournalIcon },
      { label: "Standing", icon: JournalIcon },
      { label: "Strategies", icon: ScannerIcon },
    ],
  },
  {
    label: "LABS",
    items: [
      { label: "Backtest", icon: ScannerIcon },
      { label: "Options Backtest", icon: OptionsBacktestIcon },
      { label: "GEX Replay", icon: ReplayIcon },
      { label: "Trident", icon: TridentIcon },
      { label: "Chart Saves", icon: BookmarkIcon },
      { label: "Beliefs", icon: JournalIcon },
    ],
  },
  {
    label: "SYSTEM",
    items: [{ label: "Operator Status", icon: SettingsIcon }, { label: "Settings", icon: SettingsIcon }],
  },
];

type SidebarNavProps = {
  collapsed: boolean;
  activePanel: string;
  onSelect: (label: string) => void;
};

function SidebarNav({ collapsed, activePanel, onSelect }: SidebarNavProps) {
  const [openSections, setOpenSections] = useState(() => new Set(["TODAY", "SYSTEM"]));
  return (
    <nav className="flex flex-col gap-1" aria-label="Primary">
      {NAV_SECTIONS.map((section) => {
        const containsActive = section.items.some((item) => item.label === activePanel);
        const expanded = collapsed || containsActive || openSections.has(section.label);
        return <div key={section.label}>
          {!collapsed && <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setOpenSections((current) => { const next = new Set(current); if (next.has(section.label)) next.delete(section.label); else next.add(section.label); return next; })}
            className="flex w-full items-center justify-between rounded-md px-2.5 py-2 text-[9px] font-bold uppercase hover:bg-white/[0.03]"
            style={{ letterSpacing: "0.14em", color: containsActive ? "var(--text-dim)" : "var(--text-mute)" }}
          >
            <span>{section.label}</span><ChevronLeftIcon width={12} height={12} className={cn("transition-transform", expanded ? "-rotate-90" : "rotate-180")} />
          </button>}
          {expanded && <div className="space-y-0.5 pb-1">{section.items.map((item) => {
            const Icon = item.icon;
            const isActive = activePanel === item.label;
            return (
              <button
                key={item.label}
                type="button"
                onClick={() => onSelect(item.label)}
                aria-current={isActive ? "page" : undefined}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "flex flex-row items-center gap-[10px] w-full min-h-[32px] py-[4px] rounded-[7px]",
                  "text-[12px] font-medium transition-colors duration-150 ease-in-out",
                  collapsed ? "justify-center px-0" : "px-[11px]",
                  isActive
                    ? "text-[var(--text)]"
                    : "text-[var(--text-dim)] hover:text-[var(--text)] hover:bg-[color-mix(in_srgb,var(--panel-2)_60%,transparent)]"
                )}
                style={{
                  letterSpacing: "0.01em",
                  backgroundColor: isActive ? "var(--nav-active)" : "transparent",
                }}
              >
                <Icon
                  width={16}
                  height={16}
                  className="shrink-0"
                  style={{ color: isActive ? "var(--text)" : "var(--text-mute)" }}
                />
                {!collapsed && <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>}
              </button>
            );
          })}</div>}
        </div>;
      })}
    </nav>
  );
}

type SidebarFooterProps = {
  collapsed: boolean;
  tiledMode: boolean;
  onTiledModeChange?: (tiled: boolean) => void;
  onCommandPaletteOpen?: () => void;
};

/**
 * Mode toggle + palette hint, pinned to the bottom of the nav.
 *
 * This sits in the sidebar rather than the header on purpose: the header row already
 * carries the ticker search, quote, workspace tabs and the active panel's own toolbar,
 * and the widest of those toolbars (Strike Matrix) has previously been clipped by exactly
 * this kind of addition (see the width note in Header.tsx). The sidebar's bottom is empty.
 */
function SidebarFooter({
  collapsed,
  tiledMode,
  onTiledModeChange,
  onCommandPaletteOpen,
}: SidebarFooterProps) {
  if (!onTiledModeChange && !onCommandPaletteOpen) return null;
  return (
    <div className="mt-auto flex flex-col gap-[4px] pt-3" style={{ borderTop: "1px solid var(--line)" }}>
      {onTiledModeChange && (
        <button
          type="button"
          onClick={() => onTiledModeChange(!tiledMode)}
          aria-pressed={tiledMode}
          title={tiledMode ? "Back to one panel at a time" : "Open several panels at once"}
          className={cn(
            "flex flex-row items-center gap-[8px] w-full min-h-[30px] rounded-[8px] text-[11.5px] font-semibold",
            collapsed ? "justify-center px-0" : "px-[11px]"
          )}
          style={{
            background: tiledMode ? "var(--nav-active)" : "transparent",
            border: "1px solid var(--line)",
            color: tiledMode ? "var(--text)" : "var(--text-dim)",
          }}
        >
          <GridIcon width={14} height={14} className="shrink-0" />
          {!collapsed && <span>{tiledMode ? "Workspace on" : "Workspace"}</span>}
        </button>
      )}
      {onCommandPaletteOpen && (
        <button
          type="button"
          onClick={onCommandPaletteOpen}
          title="Command palette"
          className={cn(
            "flex flex-row items-center gap-[8px] w-full min-h-[30px] rounded-[8px] text-[11.5px] font-semibold",
            collapsed ? "justify-center px-0" : "px-[11px]"
          )}
          style={{ border: "1px solid var(--line)", color: "var(--text-dim)" }}
        >
          <SearchIcon width={14} height={14} className="shrink-0" />
          {!collapsed && (
            <span className="flex flex-1 flex-row items-center justify-between">
              Commands
              <kbd
                className="rounded-[4px] px-[4px] py-[1px] text-[9px]"
                style={{
                  background: "var(--panel-2)",
                  border: "1px solid var(--line)",
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-mute)",
                }}
              >
                ⌘K
              </kbd>
            </span>
          )}
        </button>
      )}
    </div>
  );
}

type SidebarProps = {
  /**
   * Controlled mobile-drawer open state. Header.tsx's `.nav-toggle` hamburger is meant to
   * be the SAME trigger as this drawer (not a duplicate) — see the coordination note in
   * Header.tsx. When `mobileOpen` is provided, Sidebar becomes controlled: it stops
   * rendering its own built-in hamburger button (the caller, e.g. Header + a shared
   * page.tsx state, owns opening it) and reports open/close requests via
   * `onMobileOpenChange` instead of managing state internally.
   *
   * When omitted, Sidebar falls back to its original uncontrolled behavior (internal
   * state + its own hamburger button), so it still works standalone.
   */
  mobileOpen?: boolean;
  onMobileOpenChange?: (open: boolean) => void;
  /**
   * Controlled active-panel selection, same controlled/uncontrolled pattern as
   * `mobileOpen` above. When provided, page.tsx (or any parent) owns which panel is
   * selected so it can render the matching panel component and pass the name to
   * Header's `panelName` prop. Falls back to internal state when omitted.
   */
  activePanel?: string;
  onActivePanelChange?: (panel: string) => void;
  /**
   * Workspace (multi-pane) mode. Owned by the page, not here, because the page is what
   * swaps the main region between one panel and the docking grid — the sidebar only
   * renders the toggle. Omit both to hide the toggle entirely.
   */
  tiledMode?: boolean;
  onTiledModeChange?: (tiled: boolean) => void;
  /** Opens the command palette. Omit to hide the button (the Ctrl/Cmd+K shortcut is the page's). */
  onCommandPaletteOpen?: () => void;
};

export function Sidebar({
  mobileOpen: mobileOpenProp,
  onMobileOpenChange,
  activePanel: activePanelProp,
  onActivePanelChange,
  tiledMode = false,
  onTiledModeChange,
  onCommandPaletteOpen,
}: SidebarProps = {}) {
  const [collapsed, setCollapsed] = useState(false);
  const [internalActivePanel, setInternalActivePanel] = useState("Strike Matrix");
  const [internalMobileOpen, setInternalMobileOpen] = useState(false);

  const isPanelControlled = activePanelProp !== undefined;
  const activePanel = isPanelControlled ? activePanelProp : internalActivePanel;
  const setActivePanel = (panel: string) => {
    onActivePanelChange?.(panel);
    if (!isPanelControlled) setInternalActivePanel(panel);
  };

  const isControlled = mobileOpenProp !== undefined;
  const mobileOpen = isControlled ? mobileOpenProp : internalMobileOpen;
  const setMobileOpen = (open: boolean) => {
    onMobileOpenChange?.(open);
    if (!isControlled) setInternalMobileOpen(open);
  };

  const asideBase =
    "flex flex-col h-full box-border";
  const asideStyle = {
    background: "color-mix(in srgb, var(--panel) 90%, transparent)",
    borderRight: "1px solid var(--line)",
    fontFamily: "var(--font-sans)",
    fontSize: "13px",
    color: "var(--text)",
  };

  return (
    <>
      {/*
        Mobile hamburger trigger — approximate mobile drawer — verify against live site.
        Only rendered when uncontrolled: once a parent wires Header's `.nav-toggle` to this
        drawer via `mobileOpen`/`onMobileOpenChange`, that becomes the single on-screen
        trigger and this built-in one is suppressed to avoid a duplicate hamburger.
      */}
      {!isControlled && (
        <button
          type="button"
          aria-label="Open navigation"
          onClick={() => setMobileOpen(true)}
          className="lg:hidden fixed top-3 left-3 z-40 grid place-items-center w-9 h-9 rounded-[7px]"
          style={{
            background: "var(--panel)",
            border: "1px solid var(--line)",
            color: "var(--text-mute)",
          }}
        >
          <MenuIcon width={18} height={18} />
        </button>
      )}

      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/60"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Desktop sidebar */}
      <aside
        className={cn(
          asideBase,
          "hidden lg:flex",
          collapsed ? "w-[52px] min-w-[52px]" : "w-[182px] min-w-[182px]"
        )}
        style={{ ...asideStyle, padding: "12px 10px" }}
      >
        <div
          className="flex flex-row items-center justify-end h-[35px]"
          style={{ padding: "9px 9px 2px" }}
        >
          <button
            type="button"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setCollapsed((v) => !v)}
            className="side-collapse grid place-items-center w-6 h-6 rounded-[7px]"
            style={{ border: "1px solid var(--line)", color: "var(--text-mute)" }}
          >
            <ChevronLeftIcon
              width={16}
              height={16}
              className={cn("transition-transform duration-150", collapsed && "rotate-180")}
            />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <SidebarNav collapsed={collapsed} activePanel={activePanel} onSelect={setActivePanel} />
        </div>
        <SidebarFooter
          collapsed={collapsed}
          tiledMode={tiledMode}
          onTiledModeChange={onTiledModeChange}
          onCommandPaletteOpen={onCommandPaletteOpen}
        />
      </aside>

      {/* Mobile slide-in drawer — approximate mobile drawer — verify against live site */}
      <aside
        className={cn(
          asideBase,
          "lg:hidden fixed inset-y-0 left-0 z-50 w-[182px] min-w-[182px] transition-transform duration-200 ease-in-out",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        style={{ ...asideStyle, padding: "12px 10px" }}
      >
        <div
          className="flex flex-row items-center justify-end h-[35px]"
          style={{ padding: "9px 9px 2px" }}
        >
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
            className="grid place-items-center w-6 h-6 rounded-[7px]"
            style={{ border: "1px solid var(--line)", color: "var(--text-mute)" }}
          >
            <ChevronLeftIcon width={16} height={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <SidebarNav
            collapsed={false}
            activePanel={activePanel}
            onSelect={(label) => {
              setActivePanel(label);
              setMobileOpen(false);
            }}
          />
        </div>
        <SidebarFooter
          collapsed={false}
          tiledMode={tiledMode}
          onTiledModeChange={
            onTiledModeChange
              ? (next) => {
                  onTiledModeChange(next);
                  setMobileOpen(false);
                }
              : undefined
          }
          onCommandPaletteOpen={
            onCommandPaletteOpen
              ? () => {
                  onCommandPaletteOpen();
                  setMobileOpen(false);
                }
              : undefined
          }
        />
      </aside>
    </>
  );
}

export default Sidebar;
