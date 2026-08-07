"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDownIcon, RefreshIcon } from "@/components/icons";
import { findSpotInsertIndex, HeatmapCell, SpotRow, StrikeLabelCell } from "@/components/panels/HeatmapGrid";
import { ApiError, fetchMatrix, type RealMatrixResponse } from "@/lib/api";
import type { ExposureMetric, StrikeMatrixCell } from "@/types/cipher";

/**
 * Trident panel — 3 independent single-column strike heatmaps (SPY / QQQ / IWM) side by
 * side, backed by the real cipher-system /api/matrix endpoint (same data source as
 * StrikeMatrix.tsx, fetched independently per instrument). Reuses the color-scale +
 * cell/row primitives from HeatmapGrid.tsx so both panels share the exact same visual
 * language. Layout/visual spec: docs/research/components/trident.spec.md.
 */

const TICKERS = ["SPY", "QQQ", "IWM"] as const;
// 3 tickers fetched in parallel, each a full option-chain matrix computation
// (~7s cold on the core service, whose /api/matrix cache TTL is only 12s) — a 30s
// interval would nearly always cold-miss on all 3 simultaneously. 60s spaces that out.
const AUTO_REFRESH_MS = 60_000;
const MAX_EXPIRATION_INDEX = 3;

const EXPIRATION_INDEX_OPTIONS = [
  { key: 0, label: "Nearest exp" },
  { key: 1, label: "2nd exp" },
  { key: 2, label: "3rd exp" },
  { key: 3, label: "4th exp" },
];

const METRIC_OPTIONS: { label: string; value: ExposureMetric }[] = [
  { label: "GEX", value: "gex" },
  { label: "VEX", value: "vex" },
];

function formatChangePct(changePct: number): string {
  const sign = changePct >= 0 ? "+" : "";
  return `${sign}${changePct.toFixed(2)}%`;
}

function formatExpLabel(iso: string): { dateLabel: string; daysLabel: string } {
  const d = new Date(`${iso}T00:00:00Z`);
  const dateLabel = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const days = Math.max(0, Math.round((d.getTime() - Date.now()) / 86_400_000));
  return { dateLabel, daysLabel: `${days}d` };
}

function buildCells(data: RealMatrixResponse, expIso: string, metric: ExposureMetric): Map<string, StrikeMatrixCell> {
  const map = new Map<string, StrikeMatrixCell>();
  for (const row of data.rows) {
    const cell = row.cells.find((c) => c.expiration === expIso);
    if (!cell) continue;
    map.set(String(row.strike), {
      strike: row.strike,
      expirationIso: expIso,
      value: metric === "gex" ? cell.net_gex : cell.net_vex,
    });
  }
  return map;
}

// ---------------------------------------------------------------------------
// Toolbar
// ---------------------------------------------------------------------------

function PillGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div
      className="flex flex-row items-center gap-[2px] rounded-[8px] p-[2px] shrink-0"
      style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className="rounded-[6px] px-[10px] py-[5px] text-[12px] font-semibold whitespace-nowrap transition-colors duration-150"
            style={{
              background: active ? "var(--nav-active)" : "transparent",
              color: active ? "var(--text)" : "var(--text-dim)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/** Standalone bordered toggle pill — used for Snap-to-spot/golden and FC/Auto/TR/SP.
 * No corresponding real-data field is exposed by /api/matrix, so these stay visual-only
 * toggles, per the same judgment call as Night Vision's SPY/QQQ + TS overlays. */
function ToggleButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="rounded-[8px] px-[10px] py-[6px] text-[12px] font-semibold whitespace-nowrap shrink-0 transition-colors duration-150"
      style={{
        background: active ? "var(--nav-active)" : "var(--panel-2)",
        border: "1px solid var(--line)",
        color: active ? "var(--text)" : "var(--text-dim)",
        fontFamily: "var(--font-mono)",
      }}
    >
      {label}
    </button>
  );
}

function IconButton({
  onClick,
  ariaLabel,
  children,
  spinning = false,
}: {
  onClick?: () => void;
  ariaLabel: string;
  children: React.ReactNode;
  spinning?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      className="grid place-items-center w-[30px] h-[30px] rounded-[8px] shrink-0"
      style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}
    >
      <span className={spinning ? "animate-spin" : undefined} style={{ display: "flex" }}>
        {children}
      </span>
    </button>
  );
}

function ExpirationSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (idx: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = EXPIRATION_INDEX_OPTIONS.find((o) => o.key === value) ?? EXPIRATION_INDEX_OPTIONS[0];
  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex flex-row items-center gap-1.5 rounded-[8px] px-[10px] py-[6px] text-[12px] font-semibold whitespace-nowrap"
        style={{
          background: "var(--panel-2)",
          border: "1px solid var(--line)",
          color: "var(--text-dim)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {current.label}
        <ChevronDownIcon width={12} height={12} />
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-[calc(100%+6px)] z-40 flex flex-col rounded-[8px] p-[2px] min-w-full"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", boxShadow: "0 14px 38px rgba(0,0,0,0.6)" }}
        >
          {EXPIRATION_INDEX_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              role="option"
              aria-selected={opt.key === value}
              onClick={() => {
                onChange(opt.key);
                setOpen(false);
              }}
              className="rounded-[6px] px-[10px] py-[6px] text-[12px] font-semibold text-left whitespace-nowrap transition-colors duration-150"
              style={{
                background: opt.key === value ? "var(--nav-active)" : "transparent",
                color: opt.key === value ? "var(--text)" : "var(--text-dim)",
                fontFamily: "var(--font-mono)",
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Trident({ toolbarSlot = null }: { toolbarSlot?: HTMLDivElement | null } = {}) {
  const [metric, setMetric] = useState<ExposureMetric>("gex");
  const [expirationIdx, setExpirationIdx] = useState(0);
  const [snapMode, setSnapMode] = useState<"spot" | "golden">("spot");
  const [flags, setFlags] = useState({ fc: false, auto: true, tr: false, sp: false });

  const [data, setData] = useState<Record<string, RealMatrixResponse> | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);

  const toggleFlag = (key: keyof typeof flags) =>
    setFlags((prev) => ({ ...prev, [key]: !prev[key] }));

  const load = useCallback(async (signal?: AbortSignal, background = false) => {
    if (background) setIsRefreshing(true);
    else setStatus("loading");
    try {
      const results = await Promise.all(TICKERS.map((t) => fetchMatrix(t, undefined, signal)));
      const byTicker: Record<string, RealMatrixResponse> = {};
      TICKERS.forEach((t, i) => (byTicker[t] = results[i]));
      setData(byTicker);
      setStatus("ready");
      setErrorMessage("");
    } catch (err) {
      if (signal?.aborted) return;
      setStatus((prev) => (prev === "ready" ? prev : "error"));
      setErrorMessage(err instanceof ApiError ? err.message : "Failed to load Trident.");
    } finally {
      if (background) setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const interval = setInterval(() => load(undefined, true), AUTO_REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [load]);

  const columns = useMemo(() => {
    if (!data) return [];
    return TICKERS.map((ticker) => {
      const d = data[ticker];
      const idx = Math.min(expirationIdx, d.expirations.length - 1, MAX_EXPIRATION_INDEX);
      const expIso = d.expirations[idx] ?? d.expirations[0];
      const strikes = d.rows.map((r) => r.strike).sort((a, b) => b - a);
      const cells = buildCells(d, expIso, metric);
      const spot = d.quote.price_context;

      let maxAbs = 0;
      let starKey = "";
      for (const strike of strikes) {
        const cell = cells.get(String(strike));
        if (!cell) continue;
        const abs = Math.abs(cell.value);
        if (abs > maxAbs) {
          maxAbs = abs;
          starKey = String(strike);
        }
      }

      const atmStrike = strikes.reduce(
        (closest, s) => (Math.abs(s - spot) < Math.abs(closest - spot) ? s : closest),
        strikes[0] ?? spot
      );
      const spotInsertIndex = findSpotInsertIndex(strikes, spot);
      const expLabel = formatExpLabel(expIso);

      return {
        ticker,
        spot,
        changePct: d.quote.day_change_pct,
        strikes,
        cells,
        maxAbs,
        starKey,
        atmStrike,
        spotInsertIndex,
        expLabel,
      };
    });
  }, [data, expirationIdx, metric]);

  const toolbar = (
    <>
      <PillGroup options={METRIC_OPTIONS} value={metric} onChange={setMetric} />
      <ExpirationSelector value={expirationIdx} onChange={setExpirationIdx} />
      <ToggleButton label="Snap to spot" active={snapMode === "spot"} onClick={() => setSnapMode("spot")} />
      <ToggleButton label="Snap to golden" active={snapMode === "golden"} onClick={() => setSnapMode("golden")} />
      <ToggleButton label="FC" active={flags.fc} onClick={() => toggleFlag("fc")} />
      <IconButton ariaLabel="Refresh Trident" onClick={() => load(undefined, true)} spinning={isRefreshing}>
        <RefreshIcon width={15} height={15} />
      </IconButton>
      <ToggleButton label="Auto" active={flags.auto} onClick={() => toggleFlag("auto")} />
      <ToggleButton label="TR" active={flags.tr} onClick={() => toggleFlag("tr")} />
      <ToggleButton label="SP" active={flags.sp} onClick={() => toggleFlag("sp")} />
      <span className="ml-auto text-[11px] whitespace-nowrap" style={{ color: "var(--text-mute)" }}>
        SPY · QQQ · IWM
      </span>
    </>
  );

  return (
    <section
      className="trident flex flex-col gap-3"
      style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}
    >
      {/* Toolbar — portals into Header when a slot is provided; own row otherwise. */}
      {toolbarSlot ? (
        createPortal(toolbar, toolbarSlot)
      ) : (
        <div className="flex flex-row flex-wrap items-center gap-2 pb-1">{toolbar}</div>
      )}

      {status === "loading" && (
        <div
          className="flex items-center justify-center rounded-[10px] py-16 text-[13px]"
          style={{ border: "1px solid var(--line)", color: "var(--text-mute)" }}
        >
          Loading live Trident data…
        </div>
      )}

      {status === "error" && (
        <div
          className="flex flex-col items-center gap-2 rounded-[10px] py-16 text-[13px] text-center px-4"
          style={{ border: "1px solid var(--line)", color: "var(--neg)" }}
        >
          <span>{errorMessage}</span>
          <button
            type="button"
            onClick={() => load()}
            className="rounded-[6px] px-3 py-1.5 text-[12px] font-semibold"
            style={{ border: "1px solid var(--line)", color: "var(--text-dim)" }}
          >
            Retry
          </button>
        </div>
      )}

      {/* 3 independent single-column heatmaps */}
      {status === "ready" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 items-start">
          {columns.map((col) => (
            <TridentColumn key={col.ticker} column={col} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Column
// ---------------------------------------------------------------------------

type TridentColumnData = {
  ticker: string;
  spot: number;
  changePct: number;
  strikes: number[];
  cells: Map<string, StrikeMatrixCell>;
  maxAbs: number;
  starKey: string;
  atmStrike: number;
  spotInsertIndex: number;
  expLabel: { dateLabel: string; daysLabel: string };
};

function TridentColumn({ column }: { column: TridentColumnData }) {
  const isPositive = column.changePct >= 0;

  return (
    <div
      className="trident-column flex flex-col min-w-0 rounded-[10px] overflow-hidden"
      style={{ border: "1px solid var(--line)" }}
    >
      {/* Column header: ticker / price / change% / expiration label */}
      <div
        className="flex flex-row items-baseline justify-between gap-2 px-[10px] py-[8px]"
        style={{ background: "var(--bg)", borderBottom: "1px solid var(--line)" }}
      >
        <div className="flex flex-row items-baseline gap-[8px] min-w-0">
          <span className="text-[13px] font-bold" style={{ letterSpacing: "0.04em" }}>
            {column.ticker}
          </span>
          <span className="text-[12px]" style={{ color: "var(--text)" }}>
            ${column.spot.toFixed(2)}
          </span>
          <span
            className="text-[11px] font-semibold"
            style={{ color: isPositive ? "var(--accent)" : "var(--neg)" }}
          >
            {formatChangePct(column.changePct)}
          </span>
        </div>
        <span className="text-[11px] shrink-0" style={{ color: "var(--text-mute)" }}>
          {column.expLabel.dateLabel} · {column.expLabel.daysLabel}
        </span>
      </div>

      {/* Independent vertical scroll per instrument */}
      <div
        className="relative overflow-y-auto overflow-x-hidden"
        style={{ height: "620px", background: "var(--panel)" }}
      >
        <div
          className="grid"
          style={{
            display: "grid",
            gridTemplateColumns: "56px 1fr",
            fontFamily: "var(--font-mono)",
            fontSize: "13px",
            color: "var(--text)",
          }}
        >
          {column.strikes.map((strike, i) => {
            const isAtm = strike === column.atmStrike;
            const cell = column.cells.get(String(strike));
            const value = cell?.value ?? 0;
            const isStar = String(strike) === column.starKey;
            const row = (
              <>
                <StrikeLabelCell key={`k-${strike}`} strike={strike} isAtm={isAtm} />
                <HeatmapCell key={`c-${strike}`} value={value} maxAbs={column.maxAbs} isStar={isStar} />
              </>
            );
            if (i === column.spotInsertIndex) {
              return (
                <SpotRowGroup key={`spot-${strike}`}>
                  <SpotRow spot={column.spot} columnCount={2} />
                  {row}
                </SpotRowGroup>
              );
            }
            return <SpotRowGroup key={strike}>{row}</SpotRowGroup>;
          })}
          {column.spotInsertIndex === column.strikes.length && (
            <SpotRow spot={column.spot} columnCount={2} />
          )}
        </div>
      </div>
    </div>
  );
}

/** Groups a spot-row marker (or a plain row) with its siblings as CSS Grid items. */
function SpotRowGroup({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export default Trident;
