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
  TridentIcon,
  BookmarkIcon,
  ScannerIcon,
  SettingsIcon,
  MenuIcon,
} from "@/components/icons";

type IconComponent = ComponentType<SVGProps<SVGSVGElement>>;

type NavItem = {
  label: string;
  icon: IconComponent;
  cipherX?: boolean;
};

type NavSection = {
  label: string;
  items: NavItem[];
};

const NAV_SECTIONS: NavSection[] = [
  {
    label: "WORKSPACE",
    items: [
      { label: "Strike Matrix", icon: GridIcon },
      { label: "Night Vision", icon: NightVisionIcon },
      { label: "Spyglass", icon: SearchIcon },
      { label: "My Watchlists", icon: StarIcon },
      { label: "Journal", icon: JournalIcon },
    ],
  },
  {
    label: "CIPHER X",
    items: [
      { label: "Trident", icon: TridentIcon, cipherX: true },
      { label: "Chart Saves", icon: BookmarkIcon, cipherX: true },
      { label: "Setup Scanner", icon: ScannerIcon, cipherX: true },
    ],
  },
  {
    label: "ACCOUNT",
    items: [{ label: "Settings", icon: SettingsIcon }],
  },
];

function CipherXBadge() {
  return (
    <span
      className="text-[8px] font-semibold px-1 py-[1px] rounded-full whitespace-nowrap"
      style={{
        background: "color-mix(in srgb, var(--gold) 25%, transparent)",
        color: "var(--gold)",
      }}
    >
      CIPHER X
    </span>
  );
}

type SidebarNavProps = {
  collapsed: boolean;
  activePanel: string;
  onSelect: (label: string) => void;
};

function SidebarNav({ collapsed, activePanel, onSelect }: SidebarNavProps) {
  return (
    <nav className="flex flex-col gap-[2px]" aria-label="Primary">
      {NAV_SECTIONS.map((section) => (
        <div key={section.label}>
          {!collapsed && (
            <div
              className="text-[9px] font-semibold uppercase px-[11px] pt-[2px] pb-[7px]"
              style={{ letterSpacing: "0.16em", color: "var(--text-mute)" }}
            >
              {section.label}
            </div>
          )}
          {section.items.map((item) => {
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
                  // min-h, not a fixed h: the CIPHER X rows stack a badge under the
                  // label and would otherwise be clipped by the 34px box.
                  "flex flex-row items-center gap-[10px] w-full min-h-[34px] py-[4px] rounded-[8px]",
                  "text-[12.5px] font-semibold transition-colors duration-150 ease-in-out",
                  collapsed ? "justify-center px-0" : "px-[11px]",
                  isActive
                    ? "text-[var(--text)]"
                    : "text-[var(--text-dim)] hover:text-[var(--text)] hover:bg-[color-mix(in_srgb,var(--panel-2)_60%,transparent)]"
                )}
                style={{
                  letterSpacing: "0.03em",
                  backgroundColor: isActive ? "var(--nav-active)" : "transparent",
                }}
              >
                <Icon
                  width={16}
                  height={16}
                  className="shrink-0"
                  style={{ color: isActive ? "var(--text)" : "var(--text-mute)" }}
                />
                {!collapsed && (
                  // Badge stacks UNDER the label rather than beside it. Inline, the
                  // 45px badge left only ~64px for the label inside a 161px button,
                  // which clipped "Chart Saves" (needs 76px) and "Setup Scanner"
                  // (94px) to "Chart Sa…" / "Setup S…". The real product's own nav
                  // text reads "Chart Saves\nCIPHER X" — a newline, i.e. stacked —
                  // so this matches it and removes the truncation at the same time.
                  <span className="flex flex-col items-start min-w-0 flex-1 leading-tight">
                    <span className="truncate max-w-full">{item.label}</span>
                    {item.cipherX && <CipherXBadge />}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
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
};

export function Sidebar({
  mobileOpen: mobileOpenProp,
  onMobileOpenChange,
  activePanel: activePanelProp,
  onActivePanelChange,
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

        <SidebarNav collapsed={collapsed} activePanel={activePanel} onSelect={setActivePanel} />
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

        <SidebarNav
          collapsed={false}
          activePanel={activePanel}
          onSelect={(label) => {
            setActivePanel(label);
            setMobileOpen(false);
          }}
        />
      </aside>
    </>
  );
}

export default Sidebar;
