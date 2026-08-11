"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { DownloadIcon, RefreshIcon, StrikeMatrixIcon } from "@/components/icons";
import {
  ExposureLegend,
  findSpotInsertIndex,
  formatDollar,
  getCellColor,
  HeatmapCell,
  SpotRow,
  StrikeLabelCell,
} from "@/components/panels/HeatmapGrid";
import { ApiError, fetchMatrix, type RealMatrixResponse } from "@/lib/api";
import { SkeletonGrid } from "@/components/ui/skeleton";
import type {
  ExposureMetric,
  MatrixDensity,
  StrikeMatrixCell,
  StrikeMatrixExpiration,
  StrikeMatrixMode,
} from "@/types/cipher";

/**
 * Strike Matrix panel — CSS-grid heatmap of dollar exposure per (strike, expiration),
 * backed by the real cipher-system /api/matrix endpoint (proxied same-origin by
 * app/server.mjs). Layout/visual spec: docs/research/components/strike-matrix.spec.md.
 */

const AUTO_REFRESH_MS = 30_000;
// core/exposure.py: DEFAULT_MATRIX_EXPIRATIONS=12, MAX_MATRIX_EXPIRATIONS=36. Confirmed
// against the real site that Compact/Full genuinely changes how many expiration columns
// are fetched from the server (not a client-side slice of a fixed response).
const COMPACT_EXPIRATIONS = 5;
const FULL_EXPIRATIONS = 36;

type RangeKey = "3" | "6" | "12" | "all";

const RANGE_OPTIONS: { label: string; value: RangeKey }[] = [
  { label: "±3%", value: "3" },
  { label: "±6%", value: "6" },
  { label: "±12%", value: "12" },
  { label: "All", value: "all" },
];

const DENSITY_OPTIONS: { label: string; value: MatrixDensity }[] = [
  { label: "Full", value: "full" },
  { label: "Compact", value: "compact" },
];

const METRIC_OPTIONS: { label: string; value: ExposureMetric }[] = [
  { label: "GEX", value: "gex" },
  { label: "VEX", value: "vex" },
];

const MODE_OPTIONS: { label: string; value: StrikeMatrixMode }[] = [
  { label: "Matrix", value: "matrix" },
  { label: "Sniper", value: "sniper" },
];

function formatExpirations(isoList: string[], count: number): StrikeMatrixExpiration[] {
  // DTE is a CALENDAR-day difference in the viewer's local timezone, not an elapsed-
  // millisecond delta against a UTC midnight. The old instant-based math read "0d" for
  // tomorrow's expiry whenever local time was already past UTC midnight (e.g. 9pm ET =
  // 01:00 UTC next day), while the real site showed "1d" for the same column.
  const now = new Date();
  const todayLocalMs = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return isoList.slice(0, count).map((iso) => {
    const [y, m, dd] = iso.split("-").map(Number);
    const expLocalMs = new Date(y, m - 1, dd).getTime();
    const dateLabel = new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
    const days = Math.max(0, Math.round((expLocalMs - todayLocalMs) / 86_400_000));
    return { dateLabel, daysLabel: `${days}d`, iso };
  });
}

function buildCells(
  rows: RealMatrixResponse["rows"],
  expirations: StrikeMatrixExpiration[],
  metric: ExposureMetric
): Map<string, StrikeMatrixCell> {
  const isoSet = new Set(expirations.map((e) => e.iso));
  const map = new Map<string, StrikeMatrixCell>();
  for (const row of rows) {
    for (const cell of row.cells) {
      if (!isoSet.has(cell.expiration)) continue;
      const value = metric === "gex" ? cell.net_gex : cell.net_vex;
      const available = metric === "gex"
        ? cell.gex_available ?? cell.available
        : cell.vex_available ?? cell.available;
      map.set(`${row.strike}|${cell.expiration}`, {
        strike: row.strike,
        expirationIso: cell.expiration,
        value: available ? value : null,
        available,
        modeled: Boolean(cell.gamma_modeled || cell.oi_from_volume || cell.iv_min_tick),
      });
    }
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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StrikeMatrix({
  ticker = "AAPL",
  toolbarSlot = null,
}: {
  ticker?: string;
  /** DOM node (from Header's toolbarSlotRef) to portal the toolbar into. Renders inline when omitted. */
  toolbarSlot?: HTMLDivElement | null;
}) {
  const [density, setDensity] = useState<MatrixDensity>("compact");
  const [range, setRange] = useState<RangeKey>("all");
  const [metric, setMetric] = useState<ExposureMetric>("gex");
  const [mode, setMode] = useState<StrikeMatrixMode>("matrix");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [data, setData] = useState<RealMatrixResponse | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);

  const expirationCount = density === "compact" ? COMPACT_EXPIRATIONS : FULL_EXPIRATIONS;

  // Always fetch the FULL chain and narrow it in the browser.
  //
  // Measured: the server cost is the Alpaca chain fetch (4.23s of a 4.67s cold
  // request), not the depth — a full chain costs 4.41s against 4.67s for a narrow
  // band, so requesting less saves nothing and a depth change re-pays a round trip
  // for a grid we already had. The real product does the same thing, which is why
  // its range toggles are instant. The payload is large but compresses hard: SPY's
  // full chain is 1.43 MB raw, 152 KB gzipped, and the proxy now negotiates gzip.
  const depth = "all";

  const load = useCallback(
    async (signal?: AbortSignal, background = false) => {
      if (background) setIsRefreshing(true);
      else setStatus("loading");
      try {
        const res = await fetchMatrix(ticker, expirationCount, signal, depth);
        setData(res);
        setStatus("ready");
        setErrorMessage("");
      } catch (err) {
        if (signal?.aborted) return;
        setStatus((prev) => (prev === "ready" ? prev : "error"));
        setErrorMessage(err instanceof ApiError ? err.message : "Failed to load strike matrix.");
      } finally {
        if (background) setIsRefreshing(false);
      }
    },
    [ticker, expirationCount, depth]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => load(undefined, true), AUTO_REFRESH_MS);
    return () => clearInterval(interval);
  }, [load, autoRefresh]);

  const expirations = useMemo(
    () => (data ? formatExpirations(data.expirations, data.expirations.length) : []),
    [data]
  );

  const activeCells = useMemo(
    () => (data ? buildCells(data.rows, expirations, metric) : new Map<string, StrikeMatrixCell>()),
    [data, expirations, metric]
  );

  const spot = data?.quote.price_context ?? 0;

  const baseStrikes = useMemo(
    () => (data ? data.rows.map((r) => r.strike).sort((a, b) => b - a) : []),
    [data]
  );

  // Range toggles filter the grid already in memory — no refetch, so the change is
  // immediate. `all` keeps every listed strike.
  const displayStrikes = useMemo(() => {
    if (range === "all" || !spot) return baseStrikes;
    const pct = range === "3" ? 0.03 : range === "6" ? 0.06 : 0.12;
    const lo = spot * (1 - pct);
    const hi = spot * (1 + pct);
    const inBand = baseStrikes.filter((k) => k >= lo && k <= hi);
    // A very wide-striked name can have nothing inside a tight band; showing an
    // empty matrix would read as "no data" rather than "nothing at this range".
    return inBand.length ? inBand : baseStrikes;
  }, [baseStrikes, range, spot]);

  const { maxAbs, starKey } = useMemo(() => {
    let max = 0;
    let key = "";
    for (const strike of displayStrikes) {
      for (const exp of expirations) {
        const cell = activeCells.get(`${strike}|${exp.iso}`);
        if (!cell) continue;
        if (cell.value == null) continue;
        const abs = Math.abs(cell.value);
        if (abs > max) {
          max = abs;
          key = `${strike}|${exp.iso}`;
        }
      }
    }
    return { maxAbs: max, starKey: key };
  }, [displayStrikes, expirations, activeCells]);

  // Golden strike = the row holding the largest |exposure| cell, i.e. the same cell
  // the grid already paints gold and lists first under Top Pulls.
  const goldenStrike = useMemo(() => {
    const [strike] = starKey.split("|");
    const parsed = Number(strike);
    return Number.isFinite(parsed) ? parsed : null;
  }, [starKey]);

  // Auto-snap to golden: scroll the heaviest strike into view whenever the grid
  // changes. Without it the golden cell is usually off-screen on load — the strike
  // list spans the whole chain, and the one row worth looking at first is wherever
  // the exposure happens to peak. Re-runs on ticker, range and metric because each
  // can move which strike is golden.
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [autoSnap, setAutoSnap] = useState(true);

  useEffect(() => {
    if (!autoSnap || goldenStrike == null || !gridRef.current) return;
    const row = gridRef.current.querySelector<HTMLElement>(
      `[data-strike="${goldenStrike}"]`
    );
    if (!row) return;
    // `center`, not `nearest`. `nearest` leaves an already-barely-visible row where it
    // is, which on load parks the golden strike flush against the bottom edge with its
    // neighbours cut off — the surrounding strikes are most of why you want to look at
    // it. The deps are all deliberate or meaningful changes (ticker/range/metric, or
    // the peak genuinely moving), not the refresh tick, so this does not fight a user
    // who is reading a different strike between refreshes.
    row.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [autoSnap, goldenStrike, ticker, range, metric]);

  const atmStrike = useMemo(
    () =>
      displayStrikes.reduce(
        (closest, s) => (Math.abs(s - spot) < Math.abs(closest - spot) ? s : closest),
        displayStrikes[0] ?? spot
      ),
    [displayStrikes, spot]
  );

  const spotInsertIndex = useMemo(() => findSpotInsertIndex(displayStrikes, spot), [displayStrikes, spot]);

  const topPulls = useMemo(() => {
    const all: StrikeMatrixCell[] = [];
    for (const strike of displayStrikes) {
      for (const exp of expirations) {
        const cell = activeCells.get(`${strike}|${exp.iso}`);
        if (cell) all.push(cell);
      }
    }
    return all
      .filter((cell): cell is StrikeMatrixCell & { value: number } => cell.value != null)
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
      .slice(0, 6);
  }, [displayStrikes, expirations, activeCells]);

  const gridTemplateColumns = `92px repeat(${Math.max(expirations.length, 1)}, minmax(200px, 1fr))`;
  const asOfLabel = data
    ? new Date(data.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" })
    : "";

  const toolbar = (
    <>
      <IconButton ariaLabel="Toggle chart style">
        <StrikeMatrixIcon width={15} height={15} />
      </IconButton>
      <PillGroup options={DENSITY_OPTIONS} value={density} onChange={setDensity} />
      <PillGroup options={RANGE_OPTIONS} value={range} onChange={setRange} />
      <PillGroup options={METRIC_OPTIONS} value={metric} onChange={setMetric} />
      <PillGroup options={MODE_OPTIONS} value={mode} onChange={setMode} />
      <IconButton ariaLabel="Refresh matrix" onClick={() => load(undefined, true)} spinning={isRefreshing}>
        <RefreshIcon width={15} height={15} />
      </IconButton>
      <button
        type="button"
        onClick={() => setAutoRefresh((v) => !v)}
        aria-pressed={autoRefresh}
        className="shrink-0 whitespace-nowrap rounded-[8px] px-[12px] py-[7px] text-[12px] font-semibold"
        style={{
          background: autoRefresh ? "var(--nav-active)" : "var(--panel-2)",
          border: "1px solid var(--line)",
          color: autoRefresh ? "var(--text)" : "var(--text-dim)",
          fontFamily: "var(--font-mono)",
        }}
      >
        Auto refresh
      </button>
      <button
        type="button"
        onClick={() => setAutoSnap((v) => !v)}
        aria-pressed={autoSnap}
        title="Snap to golden — keep the heaviest-exposure strike in view as the grid updates"
        className="shrink-0 whitespace-nowrap rounded-[8px] px-[12px] py-[7px] text-[12px] font-semibold"
        style={{
          background: autoSnap ? "var(--nav-active)" : "var(--panel-2)",
          border: "1px solid var(--line)",
          color: autoSnap ? "var(--text)" : "var(--text-dim)",
          fontFamily: "var(--font-mono)",
        }}
      >
        Snap
      </button>
    </>
  );

  return (
    // h-full + min-h-0 so the grid below can own its vertical scrolling. Without a bounded
    // height here the grid grows to its full 2961px and `main` scrolls it, which is what
    // silently disabled the sticky expiration headers -- see the grid-scroll comment.
    <section
      className="strike-matrix flex flex-col gap-3 h-full min-h-0"
      style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}
    >
      {/* Toolbar — portals into Header when a slot is provided (matches the real site's
          single-row layout); falls back to its own row for standalone use. */}
      {toolbarSlot ? (
        createPortal(toolbar, toolbarSlot)
      ) : (
        <div className="flex flex-row items-center gap-2 overflow-x-auto pb-1">{toolbar}</div>
      )}

      <ExposureLegend />

      {status === "loading" && (
        // Shaped like the grid that follows, so the panel does not jump when the fetch lands.
        // Column count tracks the density toggle for the same reason.
        <SkeletonGrid
          label={`Loading live strike matrix for ${ticker}…`}
          rows={16}
          columns={expirationCount > 6 ? 8 : 5}
        />
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

      {status === "ready" && data && (
        <>
          <div className="flex flex-row gap-3 items-stretch flex-1 min-h-0">
            {/* Grid — owns scrolling on BOTH axes.
                It must own the vertical axis too, not just the horizontal. `overflow-x: auto`
                cannot coexist with `overflow-y: visible` -- CSS computes the other axis to
                `auto` -- so this element became the nearest scrollport for the sticky
                expiration headers while `main` did the actual vertical scrolling. The headers
                then pinned to a container that never scrolls, and since the grid auto-scrolls
                to spot on load they sat ~1040px above the viewport and were never seen: six
                columns of exposure with nothing saying which expiration each one was.
                Bounding the height here puts the scrollport and the scrolling on the same
                element, which is what makes `position: sticky` mean anything. */}
            <div
              ref={gridRef}
              className="grid-scroll relative flex-1 min-w-0 min-h-0 overflow-auto rounded-[10px]"
              style={{ border: "1px solid var(--line)" }}
            >
              <div
                className="grid"
                role="table"
                aria-label={`${data.ticker} ${metric.toUpperCase()} exposure by strike and expiration`}
                style={{
                  display: "grid",
                  gridTemplateColumns,
                  fontFamily: "var(--font-mono)",
                  fontSize: "13px",
                  color: "var(--text)",
                }}
              >
                <div role="row" style={{ display: "contents" }}>
                  {/* Corner cell — sticky on BOTH axes, highest z-index */}
                  <div
                    role="columnheader"
                    className="h-cell h-strike flex flex-row items-center justify-between gap-1"
                    style={{
                      position: "sticky",
                      top: 0,
                      left: 0,
                      zIndex: 30,
                      background: "var(--bg)",
                      padding: "12px 8px 8px 10px",
                      height: "34.667px",
                      fontSize: "11px",
                      fontWeight: 700,
                      letterSpacing: "0.66px",
                      color: "var(--text-dim)",
                    }}
                  >
                    <span>STRIKE</span>
                    <DownloadIcon width={12} height={12} style={{ color: "var(--text-mute)" }} />
                  </div>

                  {/* Expiration headers — sticky top */}
                  {expirations.map((exp) => (
                    <div
                      key={exp.iso}
                      role="columnheader"
                      aria-label={`${exp.dateLabel}, ${exp.daysLabel} to expiration`}
                      className="h-cell flex flex-col items-center justify-start"
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 20,
                        background: "var(--bg)",
                        padding: "12px 8px 8px",
                        height: "34.667px",
                        fontSize: "11px",
                        fontWeight: 700,
                        letterSpacing: "0.66px",
                        color: "var(--text-dim)",
                        textAlign: "center",
                      }}
                    >
                      <span>{exp.dateLabel}</span>
                      <span style={{ opacity: 0.85, fontSize: "9.5px", fontWeight: 600 }}>{exp.daysLabel}</span>
                    </div>
                  ))}
                </div>

                {/* Data rows, with the spot-row marker inserted at the right position */}
                {displayStrikes.map((strike, i) => {
                  const isAtm = strike === atmStrike;
                  const row = (
                    <ExpandableRow
                      key={strike}
                      strike={strike}
                      isAtm={isAtm}
                      expirations={expirations}
                      cells={activeCells}
                      maxAbs={maxAbs}
                      starKey={starKey}
                      metric={metric}
                    />
                  );
                  if (i === spotInsertIndex) {
                    return (
                      <SpotRowFragmentGroup key={`spot-${strike}`}>
                        <SpotRow spot={spot} columnCount={expirations.length + 1} />
                        {row}
                      </SpotRowFragmentGroup>
                    );
                  }
                  return row;
                })}
                {spotInsertIndex === displayStrikes.length && (
                  <SpotRow spot={spot} columnCount={expirations.length + 1} />
                )}
              </div>
            </div>

            {/* Sniper mode: docked side panel, desktop only */}
            {mode === "sniper" && (
              // min-h-0 + overflow-y-auto because the row is now items-stretch: this rail
              // fills the panel height, so a long Top Pulls list must scroll inside itself
              // rather than pushing the row taller than the viewport.
              <aside
                className="hidden lg:flex flex-col gap-1.5 w-[220px] shrink-0 min-h-0 overflow-y-auto rounded-[10px] p-2"
                style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
              >
                <div
                  className="text-[10px] font-bold uppercase px-1 pt-1 pb-1.5"
                  style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}
                >
                  Top Pulls
                </div>
                {topPulls.map((cell) => {
                  const key = `${cell.strike}|${cell.expirationIso}`;
                  const isStar = key === starKey;
                  const expLabel = expirations.find((e) => e.iso === cell.expirationIso)?.dateLabel ?? "";
                  return (
                    <div
                      key={key}
                      className="flex flex-row items-center justify-between rounded-[4px] px-2 py-[7px]"
                      style={{
                        background: cell.value == null ? "var(--panel-2)" : isStar ? "var(--gold)" : getCellColor(cell.value, maxAbs),
                        color: cell.value == null ? "var(--text-mute)" : isStar ? "rgb(21,16,0)" : "#ffffff",
                        fontSize: "11px",
                        fontWeight: 700,
                      }}
                    >
                      <span>{cell.strike}</span>
                      <span style={{ opacity: 0.8, fontSize: "10px" }}>{expLabel}</span>
                      <span>{cell.value == null ? "unknown" : formatDollar(cell.value)}</span>
                    </div>
                  );
                })}
              </aside>
            )}
          </div>

          {/* Footer status line */}
          <div
            className="flex flex-col sm:flex-row sm:items-center sm:justify-end gap-0.5 sm:gap-3 text-right"
            style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-mute)" }}
          >
            <span>
              {data.ticker} · {data.coverage.contracts.toLocaleString()} contracts ·{" "}
              {expirations.length}/{data.total_expirations_available} expirations ·{" "}
              {displayStrikes.length} strikes shown
            </span>
            <span>
              {/* Mirrors MATRIX_CACHE's TTL in core/app.py — keep the two in step. */}
              updated {asOfLabel} · server cache 60s
            </span>
          </div>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Row / cell sub-components
// ---------------------------------------------------------------------------

/** Groups a spot-row marker with the data row that follows it as CSS Grid siblings. */
function SpotRowFragmentGroup({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

function ExpandableRow({
  strike,
  isAtm,
  expirations,
  cells,
  maxAbs,
  starKey,
  metric,
}: {
  strike: number;
  isAtm: boolean;
  expirations: StrikeMatrixExpiration[];
  cells: Map<string, StrikeMatrixCell>;
  maxAbs: number;
  starKey: string;
  metric: ExposureMetric;
}) {
  return (
    <div role="row" style={{ display: "contents" }}>
      <StrikeLabelCell
        strike={strike}
        isAtm={isAtm}
        ariaLabel={`Strike ${strike}`}
      />
      {expirations.map((exp) => {
        const key = `${strike}|${exp.iso}`;
        const cell = cells.get(key);
        const value = cell?.value ?? null;
        const isStar = key === starKey;
        return (
          <HeatmapCell
            key={key}
            value={value}
            maxAbs={maxAbs}
            isStar={isStar}
            modeled={cells.get(key)?.modeled}
            ariaLabel={`${metric.toUpperCase()} ${value == null ? "unknown" : formatDollar(value)} at strike ${strike}, expiration ${exp.dateLabel}, ${exp.daysLabel} to expiration${isStar ? ", largest absolute exposure" : ""}`}
          />
        );
      })}
    </div>
  );
}

export default StrikeMatrix;
