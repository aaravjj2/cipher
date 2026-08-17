"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { RefreshIcon } from "@/components/icons";
import FlowTape from "@/components/panels/FlowTape";
import { SkeletonChart } from "@/components/ui/skeleton";
import {
  ApiError,
  fetchBars,
  fetchNightVision,
  fetchNightVisionReplay,
  type RealBar,
  type RealLevel,
  type RealNightVisionResponse,
  type RealXrayRung,
} from "@/lib/api";
import { addChartSave } from "@/lib/chartSaves";
import { buildNightVisionGeometry, isRegularSessionBar, nearestBarIndex, visibleTail } from "@/lib/nightVisionGeometry";
import type { ExposureMetric } from "@/types/cipher";

/**
 * Night Vision panel — candlestick chart with 5 overlay toggles, backed by the real
 * cipher-system /api/bars (OHLC) and /api/night-vision (GEX/VEX levels + strike grid)
 * endpoints, proxied same-origin by app/server.mjs. package.json has no charting
 * library, so this stays a hand-rolled inline SVG (same approach as before, now fed
 * real data instead of a seeded PRNG). Layout/visual spec:
 * docs/research/components/night-vision.spec.md.
 */

const AUTO_REFRESH_MS = 30_000;

type ExpirationMode = "1exp" | "compact" | "full" | "leap";
// Expiration depth per pill, matching core/exposure.py's documented UI presets
// (1 Exp=1, Compact=5, Full=12, Leap=36 — see MAX_MATRIX_EXPIRATIONS).
const EXPIRATION_COUNTS: Record<ExpirationMode, number> = {
  "1exp": 1,
  compact: 5,
  full: 12,
  leap: 36,
};

const EXPIRATION_OPTIONS: { label: string; value: ExpirationMode }[] = [
  { label: "1 Exp", value: "1exp" },
  { label: "Compact", value: "compact" },
  { label: "Full", value: "full" },
  { label: "Leap", value: "leap" },
];

type Timeframe = "1D" | "5m" | "1m" | "15m" | "1H" | "4H" | "1W" | "EOD";
const PRIMARY_TIMEFRAMES: Timeframe[] = ["1D", "5m"];
const MORE_TIMEFRAMES: Timeframe[] = ["1m", "15m", "1H", "4H", "1W", "EOD"];
const INTRADAY_TIMEFRAMES = new Set<Timeframe>(["1m", "5m", "15m", "1H", "4H"]);

type OverlayKey = "sp" | "spyQqq" | "ts" | "vp" | "xray";
const OVERLAY_DEFS: { key: OverlayKey; label: string }[] = [
  { key: "sp", label: "Exposure" },
  { key: "spyQqq", label: "SPY/QQQ" },
  { key: "ts", label: "Flow" },
  { key: "vp", label: "Profile" },
  { key: "xray", label: "X-Ray" },
];

const RANGE_OPTIONS = [30, 60, 120] as const;

function linkedReplayId(ticker: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem("cipher:night-vision-replay");
    const linked = raw ? JSON.parse(raw) as { ticker?: string; snapshot_id?: string } : null;
    return linked?.ticker === ticker && linked.snapshot_id ? linked.snapshot_id : null;
  } catch {
    return null;
  }
}

function formatDollar(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

/** Same easing shape as StrikeMatrix's heatmap scale, reused here for the SP panel. */
function gexCellColor(value: number, maxAbs: number): string {
  if (maxAbs <= 0 || value === 0) return "var(--panel-2)";
  const ratio = Math.min(Math.abs(value) / maxAbs, 1);
  const eased = Math.pow(ratio, 0.42);
  const mixPct = 8 + eased * 78;
  const token = value > 0 ? "--accent" : "--neg";
  return `color-mix(in srgb, var(${token}) ${mixPct.toFixed(1)}%, var(--panel))`;
}

function barDateLabel(iso: string, intraday: boolean): string {
  const d = new Date(iso);
  return intraday
    ? d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "America/New_York" });
}

/** Volume-by-price approximation: bins real bar volume by each bar's close price. Real
 * volume, approximate binning (true tick-level volume profile isn't exposed by the API). */
function buildVolumeProfile(bars: RealBar[], minPrice: number, maxPrice: number) {
  const buckets = 22;
  const sums = new Array(buckets).fill(0);
  const span = maxPrice - minPrice || 1;
  for (const bar of bars) {
    let idx = Math.floor(((bar.close - minPrice) / span) * buckets);
    idx = Math.min(buckets - 1, Math.max(0, idx));
    sums[idx] += bar.volume;
  }
  const maxSum = Math.max(...sums, 1);
  return sums.map((v, i) => ({
    price: minPrice + (span * (i + 0.5)) / buckets,
    volume: v / maxSum,
  }));
}

// ---------------------------------------------------------------------------
// Small shared UI atoms (mirrors StrikeMatrix.tsx's convention)
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

function ToggleButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="rounded-[8px] px-3 py-[7px] text-[12px] font-semibold whitespace-nowrap transition-colors duration-150 shrink-0"
      style={{
        background: active ? "var(--nav-active)" : "var(--panel-2)",
        border: `1px solid ${active ? "var(--nav-active)" : "var(--line)"}`,
        color: active ? "var(--text)" : "var(--text-dim)",
        fontFamily: "var(--font-mono)",
      }}
    >
      {children}
    </button>
  );
}

function TextButton({ onClick, children }: { onClick?: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[8px] px-3 py-[7px] text-[12px] font-semibold whitespace-nowrap shrink-0"
      style={{
        background: "var(--panel-2)",
        border: "1px solid var(--line)",
        color: "var(--text-dim)",
        fontFamily: "var(--font-mono)",
      }}
    >
      {children}
    </button>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span
      className="flex flex-row items-center gap-1.5 text-[11px] whitespace-nowrap"
      style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}
    >
      <span className="inline-block w-[7px] h-[7px] rounded-full shrink-0" style={{ background: color }} />
      {label}
    </span>
  );
}

function EvidenceDrawer({
  snapshot,
  dataStatus,
  providerError,
  cacheNote,
  onClose,
}: {
  snapshot: NonNullable<RealNightVisionResponse["evidence_snapshot"]>;
  dataStatus?: RealNightVisionResponse["data_status"];
  providerError?: string;
  cacheNote?: string;
  onClose: () => void;
}) {
  const freshnessColor = snapshot.freshness.status === "current" ? "var(--accent)" : "var(--gold)";
  const timeline = [
    { label: "Observed", value: snapshot.event_at ?? "Unknown" },
    { label: "Captured", value: snapshot.captured_at },
    { label: "Session", value: `${snapshot.session.market_date ?? "Unknown"} · ${snapshot.session.phase}` },
  ];
  return (
    <aside
      role="dialog"
      aria-label="Night Vision evidence details"
      className="rounded-[10px] p-3"
      style={{ border: "1px solid var(--line)", background: "var(--panel-2)" }}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]">Evidence timeline</h3>
          <p className="mt-1 text-[10px]" style={{ color: "var(--text-mute)" }}>
            {snapshot.ticker} · {snapshot.snapshot_id.slice(0, 16)}
          </p>
        </div>
        <button type="button" onClick={onClose} className="rounded-md border px-2 py-1 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-dim)" }}>
          Close
        </button>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {timeline.map((item) => (
          <div key={item.label} className="rounded-md border px-2 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
            <span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>{item.label}</span>
            <span className="mt-1 block break-words text-[10px] font-mono" style={{ color: "var(--text-dim)" }}>{item.value}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <div className="rounded-md border px-2 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
          <span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Freshness</span>
          <strong className="text-[11px]" style={{ color: freshnessColor }}>{snapshot.freshness.status}</strong>
          <span className="ml-1 text-[10px]" style={{ color: "var(--text-mute)" }}>{snapshot.freshness.age_seconds == null ? "age unknown" : `${Math.round(snapshot.freshness.age_seconds)}s old`}</span>
        </div>
        <div className="rounded-md border px-2 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
          <span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Coverage</span>
          <strong className="text-[11px]">{snapshot.coverage.status}</strong>
          <span className="ml-1 text-[10px]" style={{ color: "var(--text-mute)" }}>{snapshot.coverage.calculated_cells ?? "?"}/{snapshot.coverage.listed_cells ?? "?"} cells</span>
        </div>
        <div className="rounded-md border px-2 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
          <span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Source</span>
          <strong className="text-[11px]">{snapshot.provider} · {snapshot.feed}</strong>
          <span className="block text-[10px]" style={{ color: "var(--text-mute)" }}>{dataStatus === "stale_cache" ? "cached replay" : "provider response"}</span>
        </div>
      </div>
      {(providerError || cacheNote) && (
        <div className="mt-2 rounded-md border px-2 py-2 text-[10px]" style={{ borderColor: "color-mix(in srgb, var(--gold) 40%, var(--line))", color: "var(--gold)" }}>
          {providerError && <p>Provider note: {providerError}</p>}
          {cacheNote && <p className={providerError ? "mt-1" : undefined}>{cacheNote}</p>}
        </div>
      )}
      <div className="mt-2">
        <span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Caveats and missing inputs</span>
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[10px]" style={{ color: "var(--text-dim)" }}>
          {snapshot.missing_reasons.length ? snapshot.missing_reasons.map((reason) => <li key={reason}>{reason}</li>) : <li>No missing-input flags.</li>}
          {snapshot.caveats.slice(0, 2).map((caveat) => <li key={caveat}>{caveat}</li>)}
        </ul>
      </div>
      <p className="mt-2 text-[9px]" style={{ color: "var(--text-mute)" }}>Read-only evidence · no execution authority</p>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * X-Ray ladder — the per-strike GEX/VEX column the real product docks beside the
 * chart. Previously the "X-Ray" toggle only drew a few extra faint lines on the
 * chart, which left the panel looking almost empty next to the real one; the API
 * has been returning a full `xray` ladder all along (core/app.py night_vision).
 * Rows are colour-scaled by |value| against the ladder's own peak so the structure
 * reads at a glance, with the spot row cut in at its true position.
 */
function XRayLadder({
  rungs,
  spot,
  metric,
  onMetricChange,
  ticker,
  expLabel,
  selectedStrike,
  onSelectStrike,
}: {
  rungs: RealXrayRung[];
  spot: number;
  metric: ExposureMetric;
  onMetricChange: (m: ExposureMetric) => void;
  ticker: string;
  expLabel: string;
  selectedStrike: number | null;
  onSelectStrike: (strike: number | null) => void;
}) {
  const valueOf = (r: RealXrayRung) => (metric === "gex" ? r.net_gex : r.net_vex);
  const peak = Math.max(...rungs.map((r) => Math.abs(valueOf(r))), 1);
  // Descending strikes so the ladder reads like a price axis (high at top).
  const ordered = [...rungs].sort((a, b) => b.strike - a.strike);
  const spotIndex = ordered.findIndex((r) => r.strike <= spot);

  return (
    <aside
      className="flex flex-col w-full lg:w-[250px] shrink-0 rounded-[10px] overflow-hidden"
      style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
    >
      <div className="flex flex-row items-center justify-between gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--line)" }}>
        <span className="flex flex-row items-center gap-1.5 text-[12px] font-bold">
          <span className="w-[7px] h-[7px] rounded-full" style={{ background: "var(--accent)" }} aria-hidden="true" />
          X-RAY
        </span>
        <PillGroup
          options={[
            { label: "GEX", value: "gex" as ExposureMetric },
            { label: "VEX", value: "vex" as ExposureMetric },
          ]}
          value={metric}
          onChange={onMetricChange}
        />
      </div>

      <div className="flex flex-row items-center justify-between px-3 py-1.5 text-[10px] font-semibold uppercase"
           style={{ borderBottom: "1px solid var(--line-soft)", color: "var(--text-mute)", letterSpacing: "0.08em" }}>
        <span>Strike</span>
        <span>{metric.toUpperCase()}</span>
      </div>

      <div className="flex flex-col overflow-y-auto" style={{ maxHeight: "560px" }}>
        {ordered.map((r, i) => {
          const v = valueOf(r);
          const intensity = Math.min(Math.abs(v) / peak, 1);
          const positive = v >= 0;
          const bg = `color-mix(in srgb, ${positive ? "var(--accent)" : "var(--neg)"} ${Math.round(
            8 + intensity * 62
          )}%, transparent)`;
          return (
            <div key={r.strike}>
              {i === spotIndex && spotIndex > 0 && (
                <div
                  className="flex flex-row items-center gap-2 px-3 py-[3px] text-[10px] font-bold"
                  style={{ background: "var(--panel-2)", color: "var(--text-dim)", borderTop: "1px dashed var(--line)", borderBottom: "1px dashed var(--line)" }}
                >
                  SPOT {spot.toFixed(2)}
                </div>
              )}
              <button
                type="button"
                onClick={() => onSelectStrike(selectedStrike === r.strike ? null : r.strike)}
                aria-pressed={selectedStrike === r.strike}
                className="flex flex-row items-center justify-between gap-2 px-3 py-[5px] text-[11.5px] w-full text-left"
                style={{
                  background: bg,
                  fontFamily: "var(--font-mono)",
                  outline: selectedStrike === r.strike ? "1px solid #fff" : "none",
                  outlineOffset: "-1px",
                }}
              >
                <span style={{ color: "var(--text)", fontWeight: 700 }}>{r.strike}</span>
                <span style={{ color: positive ? "var(--text)" : "var(--neg)", fontWeight: 700 }}>
                  {formatDollar(v)}
                </span>
              </button>
            </div>
          );
        })}
      </div>

      <div className="px-3 py-1.5 text-[10px]" style={{ borderTop: "1px solid var(--line)", color: "var(--text-mute)" }}>
        {ticker} · {expLabel}
      </div>
    </aside>
  );
}

export function NightVision({
  ticker = "AAPL",
  toolbarSlot = null,
}: {
  ticker?: string;
  /** DOM node (from Header's toolbarSlotRef) to portal the overlay-toggle row into. */
  toolbarSlot?: HTMLDivElement | null;
}) {
  const [expirationMode, setExpirationMode] = useState<ExpirationMode>("1exp");
  // Night Vision is primarily an intraday hedging view — the real product opens on an
  // intraday timeframe, and a daily chart makes the GEX bands look static all session.
  const [timeframe, setTimeframe] = useState<Timeframe>("5m");
  const [moreOpen, setMoreOpen] = useState(false);

  // 5 independent overlay toggles — NOT mutually exclusive, each its own boolean.
  const [spOn, setSpOn] = useState(false);
  const [spyQqqOn, setSpyQqqOn] = useState(false);
  const [tsOn, setTsOn] = useState(false);
  const [vpOn, setVpOn] = useState(false);
  const [xrayOn, setXrayOn] = useState(false);

  const [gexMetric, setGexMetric] = useState<ExposureMetric>("gex");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const [allBars, setAllBars] = useState<RealBar[]>([]);
  const [visibleBarCount, setVisibleBarCount] = useState<number>(60);
  const [sessionView, setSessionView] = useState<"rth" | "extended">("rth");
  /** SPY/QQQ benchmark bars for the comparison overlay (fetched only when toggled). */
  const [benchBars, setBenchBars] = useState<Record<string, RealBar[]>>({});
  /** Strike highlighted by clicking an X-Ray rung — links the ladder to the chart. */
  const [selectedStrike, setSelectedStrike] = useState<number | null>(null);
  /** Crosshair position in SVG space, plus the price under the cursor. */
  const [hover, setHover] = useState<{ x: number; y: number; price: number; bar: RealBar } | null>(null);
  /** Seconds until the next auto-refresh, mirroring the real product's countdown. */
  const [secondsToRefresh, setSecondsToRefresh] = useState(Math.round(AUTO_REFRESH_MS / 1000));
  const [nightVision, setNightVision] = useState<RealNightVisionResponse | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [replayId, setReplayId] = useState<string | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  const apiTimeframe = timeframe.toLowerCase();

  useEffect(() => {
    setReplayId(linkedReplayId(ticker));
  }, [ticker]);

  const load = useCallback(
    async (signal?: AbortSignal, background = false) => {
      if (background) setIsRefreshing(true);
      else setStatus("loading");
      try {
        const effectiveReplayId = replayId ?? linkedReplayId(ticker);
        const [barsRes, nvRes] = await Promise.all([
          fetchBars(ticker, apiTimeframe, signal),
          effectiveReplayId
            ? fetchNightVisionReplay(ticker, effectiveReplayId, signal)
            : fetchNightVision(ticker, signal, EXPIRATION_COUNTS[expirationMode]),
        ]);
        setAllBars(barsRes.bars);
        setNightVision(nvRes);
        setStatus("ready");
        setErrorMessage("");
      } catch (err) {
        if (signal?.aborted) return;
        setStatus((prev) => (prev === "ready" ? prev : "error"));
        setErrorMessage(err instanceof ApiError ? err.message : "Failed to load Night Vision.");
      } finally {
        if (background) setIsRefreshing(false);
      }
    },
    [ticker, apiTimeframe, expirationMode, replayId]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh || replayId) {
      setSecondsToRefresh(Math.round(AUTO_REFRESH_MS / 1000));
      return;
    }
    // Countdown so the user can see the panel is live rather than stalled — the real
    // product shows the same next-read timer beside spot.
    setSecondsToRefresh(Math.round(AUTO_REFRESH_MS / 1000));
    const tick = setInterval(() => {
      setSecondsToRefresh((s) => (s <= 1 ? Math.round(AUTO_REFRESH_MS / 1000) : s - 1));
    }, 1000);
    const id = setInterval(() => load(undefined, true), AUTO_REFRESH_MS);
    return () => {
      clearInterval(id);
      clearInterval(tick);
    };
  }, [autoRefresh, load, replayId]);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 1600);
    return () => clearTimeout(id);
  }, [toast]);

  const intraday = INTRADAY_TIMEFRAMES.has(timeframe);
  const bars = useMemo(() => {
    const replayCutoff = nightVision?.replay?.event_at ? Date.parse(nightVision.replay.event_at) : null;
    const replayBars = replayCutoff == null || Number.isNaN(replayCutoff)
      ? allBars
      : allBars.filter((bar) => Date.parse(bar.time) <= replayCutoff);
    const sessionBars = intraday && sessionView === "rth" ? replayBars.filter((bar) => isRegularSessionBar(bar.time)) : replayBars;
    return visibleTail(sessionBars, visibleBarCount);
  }, [allBars, visibleBarCount, intraday, sessionView, nightVision?.replay?.event_at]);
  const spot = nightVision?.quote.price_context ?? 0;
  const changePct = nightVision?.quote.day_change_pct ?? 0;

  const maxPrice = useMemo(() => (bars.length ? Math.max(...bars.map((b) => b.high), spot) : spot), [bars, spot]);
  const minPrice = useMemo(() => (bars.length ? Math.min(...bars.map((b) => b.low), spot) : spot), [bars, spot]);

  // (topAbove/topBelow/weakerLevels removed — every level is now drawn as a band
  //  by `bandLevels`, so the top-2 + faint-rest split they existed for is obsolete.)

  const volumeProfile = useMemo(
    () => (vpOn && bars.length ? buildVolumeProfile(bars, minPrice, maxPrice) : []),
    [vpOn, bars, minPrice, maxPrice]
  );

  // SP docked panel: nearest-expiration GEX/VEX per strike from the real strike grid.
  const nearestExpiration = nightVision?.expirations[0];
  const gexRows = useMemo(() => {
    if (!nightVision || !nearestExpiration) return [];
    const rows: { strike: number; value: number }[] = [];
    for (const row of nightVision.rows) {
      const cell = row.cells.find((c) => c.expiration === nearestExpiration);
      if (!cell) continue;
      const available = gexMetric === "gex"
        ? cell.gex_available ?? cell.available
        : cell.vex_available ?? cell.available;
      const value = gexMetric === "gex" ? cell.net_gex : cell.net_vex;
      if (!available || value == null) continue;
      rows.push({ strike: row.strike, value });
    }
    return rows.sort((a, b) => b.strike - a.strike);
  }, [nightVision, nearestExpiration, gexMetric]);
  const gexMaxAbs = useMemo(() => Math.max(...gexRows.map((r) => Math.abs(r.value)), 1), [gexRows]);
  const gexStarStrike = useMemo(() => {
    let best = gexRows[0];
    for (const r of gexRows) if (Math.abs(r.value) > Math.abs(best?.value ?? 0)) best = r;
    return best?.strike;
  }, [gexRows]);
  const gexRowsWithSpot = useMemo(() => {
    if (!gexRows.length) return [];
    const idx = gexRows.findIndex((r) => r.strike <= spot);
    const insertAt = idx === -1 ? gexRows.length : idx;
    const out: (typeof gexRows[number] | "spot")[] = [...gexRows];
    out.splice(insertAt, 0, "spot");
    return out;
  }, [gexRows, spot]);

  const expLabel = nearestExpiration
    ? new Date(`${nearestExpiration}T00:00:00Z`).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      })
    : "";

  // --- chart geometry -------------------------------------------------------
  const VIEW_W = 1200;
  const VIEW_H = 640;
  const MARGIN = { top: 28, right: 86, bottom: 28, left: 8 };
  const plotW = VIEW_W - MARGIN.left - MARGIN.right;
  const plotH = 420;
  const volumeTop = MARGIN.top + plotH + 26;
  const volumeH = 110;
  const geometry = useMemo(
    () => buildNightVisionGeometry(
      bars, spot,
      [...(nightVision?.levels ?? []).map((level) => level.price), ...(nightVision?.session_levels?.levels ?? []).map((level) => level.price)],
      MARGIN.top, plotH,
    ),
    [bars, spot, nightVision, MARGIN.top, plotH],
  );
  const { domainMax, domainMin } = geometry;
  const priceToY = useCallback(
    (p: number) => geometry.priceToY(p),
    [geometry]
  );
  const slot = plotW / Math.max(bars.length, 1);
  const xAt = useCallback((i: number) => MARGIN.left + i * slot + slot / 2, [MARGIN.left, slot]);

  const yTicks = 6;
  const yAxisLabels = Array.from({ length: yTicks }, (_, i) => {
    const p = domainMin + ((domainMax - domainMin) * i) / (yTicks - 1);
    return { price: p, y: priceToY(p) };
  });
  const xLabelEvery = Math.max(1, Math.ceil(bars.length / 6));
  const maxVolume = Math.max(...bars.map((bar) => bar.volume || 0), 1);

  // SPY/QQQ comparison overlay. This toggle previously rendered only an "overlay
  // active" badge and drew nothing. Benchmarks are fetched lazily and normalised to
  // percent-change from their first visible bar, so a $770 index can be compared
  // against any underlying on the same axis.
  useEffect(() => {
    if (!spyQqqOn) return;
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const wanted = ["SPY", "QQQ"].filter((t) => t !== ticker.toUpperCase());
        const res = await Promise.all(wanted.map((t) => fetchBars(t, apiTimeframe, controller.signal)));
        if (cancelled) return;
        const next: Record<string, RealBar[]> = {};
        wanted.forEach((t, i) => {
            next[t] = res[i].bars;
        });
        setBenchBars(next);
      } catch {
        if (!cancelled) setBenchBars({});
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [spyQqqOn, ticker, apiTimeframe]);

  /** Benchmark series mapped into this chart's price domain via percent-change. */
  const benchSeries = useMemo(() => {
    if (!spyQqqOn || !bars.length) return [] as { key: string; color: string; d: string }[];
    const base = bars[0].close || 1;
    const colors: Record<string, string> = { SPY: "#5ac8fa", QQQ: "#ffd166" };
    return Object.entries(benchBars)
      .map(([key, series]) => {
        if (!series.length) return null;
        const b0 = series[0].close || 1;
        const sessionSeries = intraday && sessionView === "rth" ? series.filter((bar) => isRegularSessionBar(bar.time)) : series;
        const pts = visibleTail(sessionSeries, bars.length).map((bar, i) => {
          const pct = (bar.close - b0) / b0;
          return { x: xAt(i), y: priceToY(base * (1 + pct)) };
        });
        if (pts.length < 2) return null;
        return {
          key,
          color: colors[key] ?? "var(--text-dim)",
          d: pts.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x} ${pt.y}`).join(" "),
        };
      })
      .filter(Boolean) as { key: string; color: string; d: string }[];
  }, [spyQqqOn, benchBars, bars, priceToY, xAt, intraday, sessionView]);

  const gammaFlip = nightVision?.summary?.gamma_flip_level ?? null;

  /**
   * How much the single gamma-flip line is worth.
   *
   * `gamma_flip_level` is the crossing nearest spot, but a net GEX profile often crosses
   * zero many times — SPY had 13 crossings on 2026-08-12 against 1 for NVDA. Drawing both
   * cases as the same confident dashed line overstates the noisy one, so the label carries
   * the count and the line fades when the profile oscillates.
   */
  const flipCrossings = nightVision?.summary?.gamma_flip_candidates?.length ?? 0;
  const flipIsNoisy = flipCrossings > 3;

  /**
   * Provenance for the exposure surface.
   *
   * GEX is gamma x open interest, so OI dated before today means the whole surface is that
   * stale, and cells without calculable exposure are blank rather than zero. Both were in
   * the payload and neither was ever shown, so a half-empty grid built on two-day-old OI
   * looked identical to a complete one.
   */
  const provenance = useMemo(() => {
    const cov = nightVision?.coverage;
    if (!cov) return null;
    const listed = cov.listed_cells || 0;
    const calculated = cov.calculated_cells || 0;
    const coveragePct = listed > 0 ? Math.round((100 * calculated) / listed) : null;
    const oiDate = cov.open_interest_as_of ?? null;
    let oiAgeDays: number | null = null;
    if (oiDate && nightVision?.as_of) {
      const oi = Date.parse(`${oiDate}T00:00:00Z`);
      const now = Date.parse(nightVision.as_of);
      if (Number.isFinite(oi) && Number.isFinite(now)) {
        oiAgeDays = Math.max(0, Math.floor((now - oi) / 86_400_000));
      }
    }
    return { coveragePct, calculated, listed, oiDate, oiAgeDays };
  }, [nightVision]);

  const regime = useMemo(() => {
    const levels = nightVision?.levels ?? [];
    const known = levels.filter((level) => Number.isFinite(level.net_gex));
    const net = known.length ? known.reduce((sum, level) => sum + level.net_gex, 0) : null;
    return {
      label: net == null ? "Exposure unavailable" : net >= 0 ? "Positive gamma / pin risk" : "Negative gamma / expansion risk",
      net,
      flip: nightVision?.summary?.gamma_flip_level ?? null,
      pull: nightVision?.peak?.price ?? null,
      pmRange: nightVision?.premarket_range_pct ?? null,
    };
  }, [nightVision]);

  /** Ghost path projected forward of the last bar, clamped to the visible domain. */
  const ghostPath = useMemo(() => {
    const g = nightVision?.ghost ?? [];
    if (!g.length || !bars.length) return [] as { x: number; y: number }[];
    const startX = xAt(bars.length - 1);
    // Occupy the right margin rather than overlapping the last candles.
    const span = Math.min(MARGIN.right * 0.8, plotW * 0.12);
    const maxStep = Math.max(...g.map((p) => p.step), 1);
    return g
      .filter((pt) => pt.price > domainMin && pt.price < domainMax)
      .map((pt) => ({ x: startX + (pt.step / maxStep) * span, y: priceToY(pt.price) }));
  }, [nightVision, bars, plotW, domainMin, domainMax, MARGIN.right, priceToY, xAt]);


  /**
   * Levels drawn as filled bands rather than hairlines.
   *
   * The real product renders each GEX level as a thick translucent zone whose
   * height and opacity scale with |GEX|, so the dealer-hedging structure reads as
   * bands of pressure around price. Ours previously drew three 2px dotted lines
   * (top-above, top-below, peak) and hid everything else behind the X-Ray toggle,
   * which left most of the plot empty and gave no sense of relative magnitude.
   *
   * Labels are laid out with a simple greedy de-collision pass: bands are placed
   * strongest-first and a label is skipped when it would overlap one already
   * placed (or the SPOT badge). Previously every label drew at its exact level y,
   * so clustered strikes near spot stacked on top of each other and the spot badge.
   */
  // Prior-session levels, clipped to the visible price domain so off-screen lines
  // do not draw labels at the chart edge.
  const sessionLevels = useMemo(() => {
    const all = nightVision?.session_levels?.levels ?? [];
    return all.filter((l) => l.price > domainMin && l.price < domainMax);
  }, [nightVision, domainMin, domainMax]);

  const LABEL_H = 18;
  const bandLevels = useMemo(() => {
    const all = nightVision?.levels ?? [];
    if (!all.length) return [];
    const peakAbs = Math.max(...all.map((l) => Math.abs(l.abs_gex)), 1);
    const scored = all
      .map((lvl) => {
        const intensity = Math.min(Math.abs(lvl.abs_gex) / peakAbs, 1);
        const isPeak = nightVision?.peak?.price === lvl.price;
        return {
          lvl,
          y: priceToY(lvl.price),
          intensity,
          isPeak,
          color: isPeak
            ? "var(--gold)"
            : lvl.price >= spot
              ? "var(--accent)"
              : "var(--neg)",
          // 3px floor keeps weak levels visible; peak-relative growth keeps the
          // strongest band clearly dominant.
          height: 3 + intensity * 26,
        };
      })
      .sort((a, b) => b.intensity - a.intensity);

    const takenBands: number[] = [priceToY(spot)];
    return scored.map((band) => {
      const collides = takenBands.some((t) => Math.abs(t - band.y) < LABEL_H);
      if (!collides) takenBands.push(band.y);
      return { ...band, showLabel: !collides };
    });
  }, [nightVision, priceToY, spot]);

  function LevelBand({
    band,
  }: {
    band: { lvl: RealLevel; y: number; intensity: number; isPeak: boolean; color: string; height: number; showLabel: boolean };
  }) {
    const { lvl, y, intensity, color, height, showLabel } = band;
    const label = `${formatDollar(lvl.abs_gex)} · ${lvl.price}`;
    return (
      <g>
        <rect
          x={MARGIN.left}
          y={y - height / 2}
          width={plotW}
          height={height}
          fill={color}
          opacity={0.12 + intensity * 0.4}
        />
        <line x1={MARGIN.left} x2={MARGIN.left + plotW} y1={y} y2={y} stroke={color} strokeWidth={1} opacity={0.75} />
        {showLabel && (
          <>
            <rect x={MARGIN.left + plotW - 92} y={y - 9} width={92} height={LABEL_H} rx={4} fill={color} />
            <text
              x={MARGIN.left + plotW - 46}
              y={y + 4}
              textAnchor="middle"
              fontSize={10}
              fontWeight={700}
              fill={band.isPeak ? "#15100a" : "#fff"}
              fontFamily="var(--font-mono)"
            >
              {label}
            </text>
          </>
        )}
      </g>
    );
  }

  const overlayToggleRow = (
    <>
      {OVERLAY_DEFS.map((o) => {
        const active =
          o.key === "sp" ? spOn : o.key === "spyQqq" ? spyQqqOn : o.key === "ts" ? tsOn : o.key === "vp" ? vpOn : xrayOn;
        const onClick =
          o.key === "sp"
            ? () => setSpOn((v) => !v)
            : o.key === "spyQqq"
              ? () => setSpyQqqOn((v) => !v)
              : o.key === "ts"
                ? () => setTsOn((v) => !v)
                : o.key === "vp"
                  ? () => setVpOn((v) => !v)
                  : () => setXrayOn((v) => !v);
        return (
          <ToggleButton key={o.key} active={active} onClick={onClick}>
            {o.label}
          </ToggleButton>
        );
      })}
      <div className="w-px self-stretch mx-1 hidden sm:block" style={{ background: "var(--line)" }} />
      <TextButton onClick={() => setToast("Chart captured")}>Capture</TextButton>
      <TextButton
        onClick={() => {
          if (!nightVision) return;
          const ranked = [...nightVision.levels].sort((a, b) => b.abs_gex - a.abs_gex).slice(0, 4);
          const maxAbs = ranked[0]?.abs_gex || 1;
          addChartSave({
            ticker,
            price: spot,
            view: expirationMode === "1exp" ? "1 Exp" : EXPIRATION_OPTIONS.find((o) => o.value === expirationMode)?.label || "1 Exp",
            imageUrl: "",
            topLevels: ranked.map((l) => ({ level: l.price, score: Math.round((l.abs_gex / maxAbs) * 100) })),
          });
          setToast("Chart saved");
        }}
      >
        Save chart
      </TextButton>
      {toast && (
        <span className="text-[11px]" style={{ color: "var(--accent)" }}>
          {toast}
        </span>
      )}
    </>
  );

  return (
    <section
      className="night-vision flex flex-col gap-3"
      style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}
    >
      {/* Overlay-toggle row — portals into Header on the real site's single-row layout;
          falls back to its own row for standalone use. */}
      {toolbarSlot ? (
        createPortal(overlayToggleRow, toolbarSlot)
      ) : (
        <div className="flex flex-row flex-wrap items-center gap-2">{overlayToggleRow}</div>
      )}

      {/* Toolbar row 2 — expiration/timeframe pills, ticker/mode line, legend, refresh */}
      <div className="flex flex-row flex-wrap items-center gap-2">
        <PillGroup options={EXPIRATION_OPTIONS} value={expirationMode} onChange={setExpirationMode} />

        <div
          className="flex flex-row items-center gap-[2px] rounded-[8px] p-[2px] shrink-0"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
        >
          {PRIMARY_TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              aria-pressed={timeframe === tf}
              className="rounded-[6px] px-[10px] py-[5px] text-[12px] font-semibold whitespace-nowrap transition-colors duration-150"
              style={{
                background: timeframe === tf ? "var(--nav-active)" : "transparent",
                color: timeframe === tf ? "var(--text)" : "var(--text-dim)",
              }}
            >
              {tf}
            </button>
          ))}
          <div className="relative">
            <button
              type="button"
              onClick={() => setMoreOpen((v) => !v)}
              aria-expanded={moreOpen}
              className="rounded-[6px] px-[10px] py-[5px] text-[12px] font-semibold whitespace-nowrap transition-colors duration-150"
              style={{
                background: MORE_TIMEFRAMES.includes(timeframe) ? "var(--nav-active)" : "transparent",
                color: MORE_TIMEFRAMES.includes(timeframe) ? "var(--text)" : "var(--text-dim)",
              }}
            >
              {MORE_TIMEFRAMES.includes(timeframe) ? timeframe : "More"} ▾
            </button>
            {moreOpen && (
              <div
                className="absolute left-0 top-[calc(100%+4px)] z-20 flex flex-col gap-[2px] rounded-[8px] p-1"
                style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
              >
                {MORE_TIMEFRAMES.map((tf) => (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => {
                      setTimeframe(tf);
                      setMoreOpen(false);
                    }}
                    className="rounded-[5px] px-2.5 py-1 text-[12px] font-semibold text-left whitespace-nowrap"
                    style={{
                      color: timeframe === tf ? "var(--text)" : "var(--text-dim)",
                      background: timeframe === tf ? "var(--nav-active)" : "transparent",
                    }}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div role="group" className="flex items-center gap-1 rounded-lg border p-1" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }} aria-label="Visible chart range">
          {RANGE_OPTIONS.map((count) => <button key={count} type="button" aria-pressed={visibleBarCount === count} onClick={() => setVisibleBarCount(count)} className="rounded-md px-2 py-1 text-[10px] font-bold" style={{ background: visibleBarCount === count ? "var(--nav-active)" : "transparent", color: visibleBarCount === count ? "var(--text)" : "var(--text-mute)" }}>{count} bars</button>)}
        </div>

        {intraday && <div role="group" className="flex items-center gap-1 rounded-lg border p-1" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }} aria-label="Chart session">
          {(["rth", "extended"] as const).map((value) => <button key={value} type="button" aria-pressed={sessionView === value} onClick={() => setSessionView(value)} className="rounded-md px-2 py-1 text-[10px] font-bold uppercase" style={{ background: sessionView === value ? "var(--nav-active)" : "transparent", color: sessionView === value ? "var(--text)" : "var(--text-mute)" }}>{value === "rth" ? "RTH" : "All sessions"}</button>)}
        </div>}

        <span className="text-[11px]" style={{ color: "var(--text-mute)" }}>
          {ticker} · {timeframe} · sniper {expLabel}
        </span>

        {/* Exposure provenance. Stale open interest and a sparsely calculable grid both
            change what the levels mean, and neither was visible before. */}
        {provenance && (
          <span
            className="text-[10px] font-mono"
            style={{ color: (provenance.oiAgeDays ?? 0) >= 1 ? "var(--neg)" : "var(--text-mute)" }}
            title={
              `Exposure is gamma x open interest. OI from ${provenance.oiDate ?? "unknown"}` +
              `${provenance.oiAgeDays != null ? ` (${provenance.oiAgeDays}d old)` : ""}. ` +
              `${provenance.calculated} of ${provenance.listed} listed cells have calculable ` +
              `exposure; the rest are unknown, not zero.`
            }
          >
            OI {provenance.oiDate ?? "n/a"}
            {provenance.oiAgeDays != null && provenance.oiAgeDays >= 1 ? ` · ${provenance.oiAgeDays}d stale` : ""}
            {provenance.coveragePct != null ? ` · ${provenance.coveragePct}% cells` : ""}
          </span>
        )}
        {nightVision?.evidence_snapshot && (
          <button type="button" onClick={() => setEvidenceOpen((open) => !open)} className="rounded-md border px-2 py-1 text-[10px] font-mono" style={{ borderColor: nightVision.evidence_snapshot.freshness.status === "current" ? "var(--line)" : "var(--gold)", color: nightVision.evidence_snapshot.freshness.status === "current" ? "var(--text-mute)" : "var(--gold)" }} aria-expanded={evidenceOpen}>
            Evidence {nightVision.evidence_snapshot.snapshot_id.slice(0, 12)} · {nightVision.evidence_snapshot.freshness.status}
          </button>
        )}

        <div className="flex flex-row flex-wrap items-center gap-3 lg:ml-auto">
          <LegendDot color="var(--gold)" label="Top pull" />
          <LegendDot color="var(--accent)" label="Above spot" />
          <LegendDot color="var(--neg)" label="Below spot" />
          <button
            type="button"
            aria-label="Refresh chart"
            onClick={() => load(undefined, true)}
            className="grid place-items-center w-[26px] h-[26px] rounded-[7px] shrink-0"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}
          >
            <span className={isRefreshing ? "animate-spin" : undefined} style={{ display: "flex" }}>
              <RefreshIcon width={13} height={13} />
            </span>
          </button>
          <button
            type="button"
            onClick={() => setAutoRefresh((v) => !v)}
            aria-pressed={autoRefresh}
            disabled={Boolean(replayId)}
            className="rounded-[7px] px-2.5 py-1 text-[11px] font-semibold whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-40"
            style={{
              background: autoRefresh ? "var(--nav-active)" : "var(--panel-2)",
              border: "1px solid var(--line)",
              color: autoRefresh ? "var(--text)" : "var(--text-mute)",
            }}
          >
            Auto refresh
          </button>
          {autoRefresh && (
            <span
              className="text-[11px] font-semibold shrink-0"
              style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}
              title="Seconds until the next automatic refresh"
            >
              {secondsToRefresh}s
            </span>
          )}
        </div>
      </div>

      {evidenceOpen && nightVision?.evidence_snapshot && (
        <EvidenceDrawer
          snapshot={nightVision.evidence_snapshot}
          dataStatus={nightVision.data_status}
          providerError={nightVision.provider_error}
          cacheNote={nightVision.cache_note}
          onClose={() => setEvidenceOpen(false)}
        />
      )}

      {nightVision?.replay && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2" style={{ borderColor: "var(--gold)", background: "color-mix(in srgb, var(--gold) 8%, var(--panel))" }}>
          <div>
            <strong className="text-[11px]" style={{ color: "var(--gold)" }}>Frozen scanner replay</strong>
            <p className="mt-0.5 text-[9px]" style={{ color: "var(--text-dim)" }}>
              Exposure and chart cutoff are locked to {nightVision.replay.event_at ?? "an unknown event time"}. Session levels were not captured and remain unavailable.
            </p>
            <p className="mt-0.5 text-[9px]" style={{ color: "var(--text-mute)" }}>
              Snapshot identity verified · {nightVision.replay.integrity?.matrix_checksum === "verified" ? "full matrix checksum verified" : "legacy artifact without a stored full-matrix checksum"}
            </p>
          </div>
          <button type="button" onClick={() => { sessionStorage.removeItem("cipher:night-vision-replay"); setReplayId(null); setAutoRefresh(false); }} className="rounded-md border px-2.5 py-1 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text)" }}>Return to live</button>
        </div>
      )}

      {status === "ready" && <div role="region" className="grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="Night Vision regime summary">
        <div className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Regime</span><strong className="text-[11px]">{regime.label}</strong></div>
        <div className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Gamma flip</span><strong className="text-[11px]">{regime.flip?.toFixed(2) ?? "unavailable"}</strong></div>
        <div className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Top pull</span><strong className="text-[11px]">{regime.pull?.toFixed(2) ?? "unavailable"}</strong></div>
        <div className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "var(--panel)" }}><span className="block text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>PM range / coverage</span><strong className="text-[11px]">{regime.pmRange == null ? "PM unknown" : `${regime.pmRange.toFixed(2)}%`} · {provenance?.coveragePct == null ? "coverage unknown" : `${provenance.coveragePct}% cells`}</strong></div>
      </div>}
      {status === "ready" && <p className="text-[9px] leading-relaxed" style={{ color: "var(--text-mute)" }}>Filled bands = public-OI GEX heuristic, not verified dealer positioning · gray dashes = traded session levels · gold dotted path = short-horizon hedge-surface heuristic, not a forecast · missing gamma/OI stays unavailable.</p>}

      {status === "loading" && (
        // The night-vision payload measured 742 KB and 4.2 seconds warm, so this state is
        // held long enough that a centred line of text reads as a stall. Holdings and
        // Standing deliberately keep their text instead: at 3 ms and 144 ms a placeholder
        // would only flash, which is worse than the sentence it replaced.
        <SkeletonChart label={`Loading live ${ticker} chart and gamma levels…`} />
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

      {status === "ready" && (
        <div className="flex flex-col lg:flex-row gap-3 items-stretch">
          <div
            className="relative flex-1 min-w-0 rounded-[10px] overflow-hidden"
            style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
          >
            <svg
              viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
              className="w-full h-auto block"
              role="img"
              aria-label={`${ticker} candlestick chart`}
              onMouseLeave={() => setHover(null)}
              onMouseMove={(e) => {
                // Map client coords into the SVG's own viewBox space so the crosshair
                // tracks correctly at any rendered width.
                const rect = e.currentTarget.getBoundingClientRect();
                const x = ((e.clientX - rect.left) / rect.width) * VIEW_W;
                const y = ((e.clientY - rect.top) / rect.height) * VIEW_H;
                const index = nearestBarIndex(x, MARGIN.left, plotW, bars.length);
                if (index == null || y < MARGIN.top || y > volumeTop + volumeH) return setHover(null);
                const bar = bars[index];
                const inPricePlot = y <= MARGIN.top + plotH;
                const price = inPricePlot
                  ? domainMax - ((y - MARGIN.top) / plotH) * (domainMax - domainMin)
                  : bar.close;
                setHover({ x: xAt(index), y: inPricePlot ? y : priceToY(bar.close), price, bar });
              }}
            >
              <defs>
                {/* Keeps bands/paths from painting over the axes when a level or the
                    ghost path runs past the visible price domain. */}
                <clipPath id="nv-plot-clip">
                  <rect x={MARGIN.left} y={MARGIN.top} width={plotW} height={plotH} />
                </clipPath>
              </defs>

              {/* Faint centered watermark (Cipher logo mark stand-in) */}
              <g opacity={0.05} stroke="var(--text)" fill="none">
                <circle cx={VIEW_W / 2} cy={VIEW_H / 2} r={150} strokeWidth={3} />
                <circle cx={VIEW_W / 2} cy={VIEW_H / 2} r={92} strokeWidth={2} />
                <line x1={VIEW_W / 2 - 150} y1={VIEW_H / 2} x2={VIEW_W / 2 + 150} y2={VIEW_H / 2} strokeWidth={2} />
                <line x1={VIEW_W / 2} y1={VIEW_H / 2 - 150} x2={VIEW_W / 2} y2={VIEW_H / 2 + 150} strokeWidth={2} />
                <text
                  x={VIEW_W / 2}
                  y={VIEW_H / 2 + 20}
                  textAnchor="middle"
                  fontSize={80}
                  fontWeight={800}
                  fill="var(--text)"
                  stroke="none"
                  fontFamily="var(--font-mono)"
                >
                  C
                </text>
              </g>

              {/* Shared price grid. Every price-derived mark uses priceToY above. */}
              <g opacity={0.45}>
                {yAxisLabels.map((tick, index) => <line key={`grid-${index}`} x1={MARGIN.left} x2={MARGIN.left + plotW} y1={tick.y} y2={tick.y} stroke="var(--line)" strokeWidth={1} />)}
              </g>

              {/* VP overlay: volume-profile histogram along the chart's right edge (real bar volume, approximate binning) */}
              {vpOn &&
                volumeProfile.map((b, i) => {
                  const y = priceToY(b.price);
                  const rowH = plotH / (volumeProfile.length - 1);
                  const w = 8 + b.volume * 130;
                  return (
                    <rect
                      key={i}
                      x={MARGIN.left + plotW - w}
                      y={y - rowH / 2 + 1}
                      width={w}
                      height={Math.max(rowH - 2, 1)}
                      fill="var(--accent)"
                      opacity={0.28}
                    />
                  );
                })}

              {/* True time-volume panel sharing the exact candle x slots. */}
              <g aria-label="Volume bars">
                <line x1={MARGIN.left} x2={MARGIN.left + plotW} y1={volumeTop} y2={volumeTop} stroke="var(--line)" />
                <text x={MARGIN.left + 4} y={volumeTop + 12} fontSize={9} fill="var(--text-mute)" fontFamily="var(--font-mono)">VOLUME</text>
                {bars.map((bar, index) => {
                  const height = Math.max(1, ((bar.volume || 0) / maxVolume) * (volumeH - 16));
                  return <rect key={`volume-${bar.time}`} x={xAt(index) - slot * 0.28} y={volumeTop + volumeH - height} width={Math.max(slot * 0.56, 1)} height={height} fill={bar.close >= bar.open ? "var(--accent)" : "var(--neg)"} opacity={0.35} />;
                })}
              </g>

              {/* Candlesticks — purple up / red down, NOT green, per this app's convention */}
              {bars.map((bar, i) => {
                const up = bar.close >= bar.open;
                const color = up ? "var(--accent)" : "var(--neg)";
                const x = xAt(i);
                const bodyTop = priceToY(Math.max(bar.open, bar.close));
                const bodyBottom = priceToY(Math.min(bar.open, bar.close));
                const bodyH = Math.max(bodyBottom - bodyTop, 1.5);
                const bodyW = slot * 0.56;
                return (
                  <g key={bar.time}>
                    <line x1={x} x2={x} y1={priceToY(bar.high)} y2={priceToY(bar.low)} stroke={color} strokeWidth={1.4} />
                    <rect x={x - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} />
                  </g>
                );
              })}

              {/* Every GEX level as a magnitude-scaled band (gold = top pull). */}
              <g clipPath="url(#nv-plot-clip)">
                {bandLevels.map((band) => (
                  <LevelBand key={`band-${band.lvl.price}`} band={band} />
                ))}
              </g>

              {/* Prior-session and extended-hours levels (PDH/PDL, PWH/PWL, PMH/PML,
                  post-market). Drawn as dashed neutral lines so they read as structure
                  rather than exposure — they come from traded price, not from the
                  options surface, and the two should not look alike. */}
              <g clipPath="url(#nv-plot-clip)">
                  {sessionLevels.map((lvl) => (
                    <g key={`sess-${lvl.kind}-${lvl.price}`}>
                      <line
                        x1={MARGIN.left}
                        x2={MARGIN.left + plotW}
                        y1={priceToY(lvl.price)}
                        y2={priceToY(lvl.price)}
                        stroke="var(--text-mute)"
                        strokeWidth={1}
                        strokeDasharray="2 5"
                        opacity={0.75}
                      />
                      <text
                        x={MARGIN.left + 4}
                        y={priceToY(lvl.price) - 3}
                        fontSize={9}
                        fontFamily="var(--font-mono)"
                        fill="var(--text-mute)"
                      >
                        {lvl.label} {lvl.price}
                      </text>
                    </g>
                  ))}
              </g>

              {/* Gamma flip — the level where net dealer gamma changes sign. Returned
                  by the API as summary.gamma_flip_level and previously never drawn,
                  despite being the single most-watched line on a hedging chart. */}
              {gammaFlip != null && gammaFlip > domainMin && gammaFlip < domainMax && (
                <g>
                  <line
                    x1={MARGIN.left}
                    x2={MARGIN.left + plotW}
                    y1={priceToY(gammaFlip)}
                    y2={priceToY(gammaFlip)}
                    stroke="var(--text-dim)"
                    strokeWidth={flipIsNoisy ? 1 : 1.5}
                    strokeDasharray={flipIsNoisy ? "3 6" : "7 5"}
                    opacity={flipIsNoisy ? 0.5 : 0.9}
                  />
                  <text
                    x={MARGIN.left + 6}
                    y={priceToY(gammaFlip) - 5}
                    fontSize={9.5}
                    fontWeight={700}
                    fill="var(--text-dim)"
                    fontFamily="var(--font-mono)"
                    opacity={flipIsNoisy ? 0.75 : 1}
                  >
                    GAMMA FLIP {gammaFlip.toFixed(2)}
                    {flipCrossings > 1 ? ` · 1 of ${flipCrossings} crossings` : ""}
                  </text>
                </g>
              )}

              {/* SPY/QQQ benchmark comparison, normalised to percent-change. */}
              {benchSeries.map((b) => (
                <g key={b.key}>
                  <path d={b.d} fill="none" stroke={b.color} strokeWidth={1.4} opacity={0.85} />
                </g>
              ))}

              {/* Ghost — heuristic short-horizon path projected from the hedging
                  surface. Also already in the payload and never rendered. Drawn
                  forward of the last bar and deliberately faint: it is a magnet
                  projection, not a forecast. */}
              {ghostPath.length > 1 && (
                <g opacity={0.75}>
                  <path
                    d={ghostPath.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x} ${pt.y}`).join(" ")}
                    fill="none"
                    stroke="var(--gold)"
                    strokeWidth={1.6}
                    strokeDasharray="2 3"
                  />
                  <circle cx={ghostPath[ghostPath.length - 1].x} cy={ghostPath[ghostPath.length - 1].y} r={2.6} fill="var(--gold)" />
                </g>
              )}

              {/* Gold spot-price line */}
              <g>
                <line
                  x1={MARGIN.left}
                  x2={MARGIN.left + plotW}
                  y1={priceToY(spot)}
                  y2={priceToY(spot)}
                  stroke="var(--gold)"
                  strokeWidth={2}
                  strokeDasharray="3 4"
                />
                <rect x={MARGIN.left + plotW - 84} y={priceToY(spot) - 10} width={84} height={18} rx={4} fill="var(--gold)" />
                <text
                  x={MARGIN.left + plotW - 42}
                  y={priceToY(spot) + 4}
                  textAnchor="middle"
                  fontSize={10}
                  fontWeight={800}
                  fill="#15100a"
                  fontFamily="var(--font-mono)"
                >
                  SPOT {spot.toFixed(2)}
                </text>
              </g>

              {/* Y axis price labels */}
              {yAxisLabels.map((t, i) => (
                <text
                  key={i}
                  x={VIEW_W - MARGIN.right + 12}
                  y={t.y + 4}
                  fontSize={11}
                  fill="var(--text-mute)"
                  fontFamily="var(--font-mono)"
                >
                  {t.price.toFixed(2)}
                </text>
              ))}

              {/* X axis date/time labels */}
              {bars.map((bar, i) =>
                i % xLabelEvery === 0 ? (
                  <text
                    key={i}
                    x={xAt(i)}
                    y={VIEW_H - 8}
                    textAnchor="middle"
                    fontSize={11}
                    fill="var(--text-mute)"
                    fontFamily="var(--font-mono)"
                  >
                    {barDateLabel(bar.time, intraday)}
                  </text>
                ) : null
              )}

              {/* Strike selected in the X-Ray ladder — links the two views, which were
                  previously completely independent. */}
              {selectedStrike != null && selectedStrike > domainMin && selectedStrike < domainMax && (
                <g>
                  <line
                    x1={MARGIN.left}
                    x2={MARGIN.left + plotW}
                    y1={priceToY(selectedStrike)}
                    y2={priceToY(selectedStrike)}
                    stroke="#fff"
                    strokeWidth={1.5}
                  />
                  <rect x={MARGIN.left + 2} y={priceToY(selectedStrike) - 9} width={54} height={18} rx={4} fill="#fff" />
                  <text
                    x={MARGIN.left + 29}
                    y={priceToY(selectedStrike) + 4}
                    textAnchor="middle"
                    fontSize={10}
                    fontWeight={800}
                    fill="#111"
                    fontFamily="var(--font-mono)"
                  >
                    {selectedStrike}
                  </text>
                </g>
              )}

              {/* Crosshair + price readout under the cursor. */}
              {hover && (
                <g pointerEvents="none">
                  <line x1={MARGIN.left} x2={MARGIN.left + plotW} y1={hover.y} y2={hover.y} stroke="var(--text-dim)" strokeWidth={0.8} strokeDasharray="2 3" opacity={0.8} />
                  <line x1={hover.x} x2={hover.x} y1={MARGIN.top} y2={volumeTop + volumeH} stroke="var(--text-dim)" strokeWidth={0.8} strokeDasharray="2 3" opacity={0.8} />
                  <rect x={MARGIN.left + plotW + 2} y={hover.y - 9} width={62} height={18} rx={4} fill="var(--panel-2)" stroke="var(--line)" />
                  <text
                    x={MARGIN.left + plotW + 33}
                    y={hover.y + 4}
                    textAnchor="middle"
                    fontSize={10}
                    fontWeight={700}
                    fill="var(--text)"
                    fontFamily="var(--font-mono)"
                  >
                    {hover.price.toFixed(2)}
                  </text>
                </g>
              )}
            </svg>

            {hover && <div className="pointer-events-none absolute left-3 top-3 rounded-lg border px-3 py-2 text-[10px] shadow-lg" style={{ borderColor: "var(--line)", background: "color-mix(in srgb, var(--panel) 94%, transparent)", color: "var(--text-dim)" }}>
              <div className="mb-1 font-bold" style={{ color: "var(--text)" }}>{barDateLabel(hover.bar.time, intraday)}</div>
              <div className="grid grid-cols-5 gap-2"><span>O {hover.bar.open.toFixed(2)}</span><span>H {hover.bar.high.toFixed(2)}</span><span>L {hover.bar.low.toFixed(2)}</span><span>C {hover.bar.close.toFixed(2)}</span><span>V {Math.round(hover.bar.volume).toLocaleString()}</span></div>
            </div>}

            {/*
              SPY/QQQ, TS: no corresponding real-data field is exposed by /api/night-vision —
              approximate as a simple active-state badge, per spec. Verify against live site
              if higher fidelity is needed.
            */}
            {(spyQqqOn || tsOn) && (
              <div className="absolute top-2 left-2 flex flex-row flex-wrap items-center gap-2">
                {spyQqqOn &&
                  benchSeries.map((b) => (
                    <span key={b.key} className="flex flex-row items-center gap-1.5 text-[10px] font-semibold" style={{ color: "var(--text-dim)" }}>
                      <span className="w-[10px] h-[2px] rounded-full" style={{ background: b.color }} aria-hidden="true" />
                      {b.key} (% change)
                    </span>
                  ))}
                {spyQqqOn && benchSeries.length === 0 && (
                  <span className="text-[10px] font-semibold" style={{ color: "var(--text-mute)" }}>
                    Loading SPY/QQQ comparison…
                  </span>
                )}
                {tsOn && (
                  <span className="rounded-full px-2 py-0.5 text-[10px] font-semibold" style={{ background: "var(--nav-active)", color: "var(--text-dim)" }}>
                    TS · Flow Tape
                  </span>
                )}
              </div>
            )}
          </div>

          {/* TS: the real product's FLOW TAPE, docked under the chart. */}
          {tsOn && <FlowTape ticker={ticker} />}

          {/* X-Ray: docked per-strike ladder, mirroring the real product's panel. */}
          {xrayOn && (nightVision?.xray?.length ?? 0) > 0 && (
            <XRayLadder
              rungs={nightVision!.xray!}
              spot={spot}
              metric={gexMetric}
              onMetricChange={setGexMetric}
              ticker={ticker}
              expLabel={expLabel}
              selectedStrike={selectedStrike}
              onSelectStrike={setSelectedStrike}
            />
          )}
          {xrayOn && !(nightVision?.xray?.length ?? 0) && <aside className="flex w-full items-center justify-center rounded-[10px] border p-6 text-[11px] lg:w-[250px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>Strike X-Ray unavailable: no calculable exposure rungs.</aside>}

          {/* SP overlay: docked right-side single-column heatmap panel (Strike-Matrix style), real GEX/VEX */}
          {spOn && (
            <aside
              className="flex flex-col w-full lg:w-[240px] shrink-0 rounded-[10px] overflow-hidden"
              style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
            >
              <div className="flex flex-row items-center justify-between gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--line)" }}>
                <div className="flex flex-col min-w-0">
                  <span className="text-[12px] font-bold truncate">
                    {ticker} <span style={{ color: "var(--text-mute)", fontWeight: 600 }}>${spot.toFixed(2)}</span>{" "}
                    <span style={{ color: changePct >= 0 ? "var(--accent)" : "var(--neg)", fontWeight: 600 }}>
                      {changePct >= 0 ? "+" : ""}
                      {changePct.toFixed(2)}%
                    </span>
                  </span>
                  <span className="text-[10px]" style={{ color: "var(--text-mute)" }}>
                    {expLabel}
                  </span>
                </div>
                <PillGroup
                  options={[
                    { label: "GEX", value: "gex" as ExposureMetric },
                    { label: "VEX", value: "vex" as ExposureMetric },
                  ]}
                  value={gexMetric}
                  onChange={setGexMetric}
                />
              </div>
              <div className="flex flex-col overflow-y-auto" style={{ maxHeight: 420 }}>
                {!gexRowsWithSpot.length && <div className="p-4 text-[11px]" style={{ color: "var(--text-mute)" }}>Exposure unavailable. Missing gamma/OI remains unknown.</div>}
                {gexRowsWithSpot.map((item) =>
                  item === "spot" ? (
                    <div
                      key="spot-marker"
                      className="flex items-center px-3 py-1"
                      style={{
                        borderTop: "1px dashed var(--gold)",
                        borderBottom: "1px dashed var(--gold)",
                        background: "color-mix(in srgb, var(--gold) 12%, transparent)",
                      }}
                    >
                      <span className="text-[10px] font-bold" style={{ color: "var(--gold)" }}>
                        SPOT {spot.toFixed(2)}
                      </span>
                    </div>
                  ) : (
                    <div
                      key={item.strike}
                      className="flex flex-row items-center justify-between px-3 py-[7px] text-[11px] font-bold"
                      style={{
                        background: item.strike === gexStarStrike ? "var(--gold)" : gexCellColor(item.value, gexMaxAbs),
                        color: item.strike === gexStarStrike ? "rgb(21,16,0)" : "#ffffff",
                        borderTop: "1px solid rgba(0,0,0,0.15)",
                      }}
                    >
                      <span>{item.strike}</span>
                      <span>{formatDollar(item.value)}</span>
                    </div>
                  )
                )}
              </div>
              <div className="px-3 py-1.5 text-[10px]" style={{ color: "var(--text-mute)", borderTop: "1px solid var(--line)" }}>
                {ticker} · {EXPIRATION_OPTIONS.find((o) => o.value === expirationMode)?.label}
              </div>
            </aside>
          )}
        </div>
      )}
    </section>
  );
}

export default NightVision;
