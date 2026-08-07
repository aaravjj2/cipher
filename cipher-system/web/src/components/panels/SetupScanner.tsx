"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DownloadIcon } from "@/components/icons";
import {
  ApiError,
  fetchFlashAgenticLive,
  fetchScanJob,
  listScanHistory,
  loadSavedScan,
  startScanJob,
  type FlashAgenticLive,
  type FlashAgenticRow,
  type RealClusterSetup,
  type RealScanCard,
  type RealScanJob,
  type SavedScanEntry,
  type ScanMode,
  type ScanStrategy,
} from "@/lib/api";
import type { ScannerResultCard } from "@/types/cipher";

/**
 * Setup Scanner panel — mode/scan-type controls, an async "Cipher Model Scan" against the
 * real cipher-system universe (core/app.py's start_scan_job / get_scan_job, polled via
 * /api/scan/job), then a card-grid of ranked results. Built from
 * docs/research/components/setup-scanner.spec.md against the three reference screenshots
 * (empty / scanning / results).
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MODE_OPTIONS: { label: string; value: ScanMode }[] = [
  { label: "Short term", value: "short" },
  { label: "Long term", value: "long" },
  { label: "LEAP", value: "leap" },
];

const HELPER_TEXT: Record<ScanMode, string> = {
  short: "Short term scans options expiring within ~15 market days.",
  long: "Long term scans options expiring within ~90 market days.",
  leap: "LEAP scans options expiring more than a year out.",
};

// core/scanner.py's cluster_exp accepts "nearest" or a 0-based expiration index string.
const CLUSTER_EXP_OPTIONS: { label: string; value: string }[] = [
  { label: "Nearest (1 Exp)", value: "nearest" },
  { label: "Next (2 Exp)", value: "1" },
  { label: "Third (3 Exp)", value: "2" },
];

const POLL_MS = 1500;
// The real product's Flash Agentic panel reads roughly every 8s ("next read 8s").
const AGENTIC_POLL_MS = 8000;
const RESULT_LIMIT = 30;
const FLASH_STRATEGIES = new Set<ScanStrategy>(["flash", "flash_index", "flash_agentic"]);

/** Trims trailing zeros without mangling values that need decimals (e.g. 1.5, 34.5). */
function fmt(n: number): string {
  return Number(n.toFixed(2)).toString();
}

function fmtList(values: number[]): string {
  return values.map(fmt).join(", ");
}

function toCard(c: RealScanCard, rank: number): ScannerResultCard {
  return {
    rank,
    ticker: c.ticker,
    direction: c.direction === "BULLISH" ? "bullish" : "bearish",
    score: Math.round(c.score),
    majorSupports: c.supports,
    majorResistances: c.resistances,
    pullTarget: c.pull_target,
    vacuumTargets: c.vacuum_targets,
    cipherRead: c.read || c.reason,
  };
}

function downloadCsv(cards: ScannerResultCard[]) {
  const header = ["Rank", "Ticker", "Direction", "Score", "Supports", "Resistances", "Pull Target", "Vacuum Targets"];
  const lines = cards.map((c) =>
    [
      c.rank,
      c.ticker,
      c.direction,
      c.score,
      `"${fmtList(c.majorSupports)}"`,
      `"${fmtList(c.majorResistances)}"`,
      fmt(c.pullTarget),
      `"${fmtList(c.vacuumTargets)}"`,
    ].join(",")
  );
  const csv = [header.join(","), ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `cipher-scan-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadClusterCsv(cards: RealScanCard[]) {
  const header = ["Rank", "Ticker", "Kind", "Side", "Strength", "Spot", "Cluster Target", "Peak Count"];
  const lines = cards.map((c, i) =>
    [
      i + 1,
      c.ticker,
      c.cluster?.kind ?? "",
      c.cluster?.side ?? "",
      fmt(c.strength ?? 0),
      fmt(c.spot),
      fmt(c.pull_target),
      c.cluster?.peak_count ?? "",
    ].join(",")
  );
  const csv = [header.join(","), ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `cipher-cluster-scan-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Small building blocks (styling follows sibling panels' inline-token convention)
// ---------------------------------------------------------------------------

function PillGroup<T extends string>({
  options,
  value,
  onChange,
  disabled,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
  disabled?: boolean;
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
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className="rounded-[6px] px-[12px] py-[7px] text-[12.5px] font-semibold whitespace-nowrap transition-colors duration-150 disabled:opacity-60 disabled:cursor-not-allowed"
            style={{
              background: active ? "var(--nav-active)" : "transparent",
              color: active ? "var(--text)" : "var(--text-dim)",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function BetaBadge() {
  return (
    <span
      className="text-[9px] font-bold px-[7px] py-[2px] rounded-full shrink-0"
      style={{ background: "var(--gold)", color: "#241a02", letterSpacing: "0.04em" }}
    >
      BETA
    </span>
  );
}

function OutlineButton({
  children,
  tone = "purple",
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  tone?: "purple" | "gold";
  onClick?: () => void;
  disabled?: boolean;
}) {
  const color = tone === "gold" ? "var(--gold)" : "var(--accent)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex flex-row items-center gap-2 rounded-[8px] px-[14px] py-[9px] text-[12.5px] font-semibold whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
      style={{
        border: `1px solid color-mix(in srgb, ${color} 55%, transparent)`,
        color,
        background: "color-mix(in srgb, " + color + " 6%, transparent)",
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Result card
// ---------------------------------------------------------------------------

function DataRow({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-row items-baseline justify-between gap-3 py-[5px]">
      <span
        className="text-[10.5px] font-semibold uppercase shrink-0"
        style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}
      >
        {label}
      </span>
      <span
        className="text-[13px] font-semibold text-right"
        style={{ fontFamily: "var(--font-mono)", color }}
      >
        {value}
      </span>
    </div>
  );
}

function ResultCard({ card }: { card: ScannerResultCard }) {
  const isBullish = card.direction === "bullish";
  const directionColor = isBullish
    ? "color-mix(in srgb, var(--accent) 78%, white)"
    : "color-mix(in srgb, var(--neg) 85%, white)";
  const supportColor = "color-mix(in srgb, var(--neg) 82%, #ff9a6b 22%)";
  const resistColor = "color-mix(in srgb, var(--accent) 75%, white 20%)";

  return (
    <div
      className="flex flex-col rounded-[12px] overflow-hidden"
      style={{
        border: "1px solid color-mix(in srgb, var(--accent) 40%, var(--line))",
        background:
          "radial-gradient(130% 100% at 0% 0%, color-mix(in srgb, var(--accent) 16%, transparent), var(--panel) 55%)",
        padding: "18px 20px",
      }}
    >
      {/* Header row */}
      <div className="flex flex-row items-start justify-between gap-2">
        <div className="flex flex-row items-center gap-2 min-w-0 flex-wrap">
          <span className="text-[13px] font-semibold" style={{ color: "var(--text-mute)" }}>
            #{card.rank}
          </span>
          <span
            className="text-[16px] font-bold"
            style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}
          >
            ${card.ticker}
          </span>
          <span
            className="text-[10px] font-bold px-[10px] py-[3px] rounded-full uppercase shrink-0"
            style={{
              border: `1px solid color-mix(in srgb, ${directionColor} 60%, transparent)`,
              color: directionColor,
              letterSpacing: "0.06em",
            }}
          >
            {isBullish ? "Bullish" : "Bearish"}
          </span>
        </div>
        <div className="text-right shrink-0">
          <span className="text-[22px] font-bold" style={{ color: "var(--text)" }}>
            {card.score}
          </span>
          <span className="text-[12px] font-semibold" style={{ color: "var(--text-mute)" }}>
            /100
          </span>
        </div>
      </div>

      {/* Data rows */}
      <div
        className="mt-3 pt-1"
        style={{ borderTop: "1px solid var(--line-soft)" }}
      >
        <DataRow label="Major Supports" value={fmtList(card.majorSupports)} color={supportColor} />
        <DataRow label="Major Resistances" value={fmtList(card.majorResistances)} color={resistColor} />
        <DataRow label="Pull Target" value={fmt(card.pullTarget)} color="var(--gold)" />
        <DataRow label="Vacuum Targets" value={fmtList(card.vacuumTargets)} color={resistColor} />
      </div>

      {/* Cipher Read */}
      <div className="mt-4">
        <div
          className="text-[11px] font-semibold uppercase mb-2"
          style={{ letterSpacing: "0.1em", color: "var(--text-mute)" }}
        >
          Cipher Read
        </div>
        <p className="text-[13.5px]" style={{ color: "var(--text-dim)", lineHeight: 1.5 }}>
          {card.cipherRead}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cluster result card — a genuinely different layout from the Cipher Model card above.
// Confirmed against the real site: cluster-scan results show a tier badge ("QUAD UPSIDE" /
// "TRIPLE DOWNSIDE"), SPOT/CLUSTER TARGET/STRENGTH stats, and pill badges for the top
// individual peaks — no score/100 or narrative text at all. The real backend already
// computes all of this (core/weight_lab.py's score_cluster_setup(), surfaced via the
// scan response's `cluster` + `setups` fields) — this card just displays it.
// ---------------------------------------------------------------------------

const CLUSTER_KIND_LABELS: Record<string, string> = {
  quad: "QUAD",
  triple: "TRIPLE",
  battle: "BATTLE",
  golden: "GOLDEN",
  call_wall: "CALL WALL",
  put_floor: "PUT FLOOR",
};

/** One strike of a cluster, shown as "<weight> · <strike>" like the real product. */
function ClusterLevelPill({ weight, strike, isAbove, isTop }: { weight: number; strike: number; isAbove: boolean; isTop: boolean }) {
  return (
    <span
      className="rounded-[4px] px-2 py-[3px] text-[11px] font-bold"
      style={{
        fontFamily: "var(--font-mono)",
        color: isTop ? "rgb(21,16,0)" : "#ffffff",
        background: isTop ? "var(--gold)" : isAbove ? "color-mix(in srgb, var(--accent) 70%, black)" : "color-mix(in srgb, var(--neg) 70%, black)",
      }}
    >
      {Math.round(weight)} · {fmt(strike)}
    </span>
  );
}

function ClusterPeakPill({ setup, maxStrength, isTop }: { setup: RealClusterSetup; maxStrength: number; isTop: boolean }) {
  const isAbove = setup.side === "above";
  const normalized = maxStrength > 0 ? Math.round((setup.strength / maxStrength) * 100) : 0;
  const strike = fmt(setup.center);
  return (
    <span
      className="rounded-[4px] px-2 py-[3px] text-[11px] font-bold"
      style={{
        fontFamily: "var(--font-mono)",
        color: isTop ? "rgb(21,16,0)" : "#ffffff",
        background: isTop ? "var(--gold)" : isAbove ? "color-mix(in srgb, var(--accent) 70%, black)" : "color-mix(in srgb, var(--neg) 70%, black)",
      }}
    >
      {normalized} · {strike}
    </span>
  );
}

function ClusterResultCard({ card, rank }: { card: RealScanCard; rank: number }) {
  const cluster = card.cluster;
  if (!cluster) return null;
  const isAbove = cluster.side === "above";
  const tone = isAbove ? "var(--accent)" : "var(--neg)";
  const kindLabel = CLUSTER_KIND_LABELS[cluster.kind] ?? cluster.kind.toUpperCase();
  const sideLabel = isAbove ? "UPSIDE" : "DOWNSIDE";

  const peaks = (card.setups && card.setups.length > 0 ? card.setups : [cluster])
    .slice()
    .sort((a, b) => b.strength - a.strength)
    .slice(0, 4);
  const maxStrength = Math.max(...peaks.map((p) => p.strength), 1);
  const topStrike = peaks[0]?.center;

  return (
    <div
      className="flex flex-col rounded-[12px] overflow-hidden"
      style={{
        border: `1px solid color-mix(in srgb, ${tone} 45%, var(--line))`,
        background: `radial-gradient(130% 100% at 0% 0%, color-mix(in srgb, ${tone} 14%, transparent), var(--panel) 55%)`,
        padding: "18px 20px",
      }}
    >
      <div className="flex flex-row items-start justify-between gap-2">
        <div className="flex flex-row items-center gap-2 min-w-0 flex-wrap">
          <span className="text-[13px] font-semibold" style={{ color: "var(--text-mute)" }}>
            #{rank}
          </span>
          <span className="text-[16px] font-bold" style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
            ${card.ticker}
          </span>
          <span
            className="text-[10px] font-bold px-[10px] py-[3px] rounded-full uppercase shrink-0"
            style={{ border: `1px solid color-mix(in srgb, ${tone} 60%, transparent)`, color: tone, letterSpacing: "0.06em" }}
          >
            {kindLabel} {sideLabel}
          </span>
        </div>
      </div>

      <div className="mt-3 pt-1" style={{ borderTop: "1px solid var(--line-soft)" }}>
        <DataRow label="Spot" value={fmt(card.spot)} color="var(--text)" />
        {/* card.target (not pull_target) is the cluster's own target strike — the
            highest-OI strike in the cluster band, per core/scanner.py's
            _detect_cluster_zones(). pull_target is the generic Cipher-model magnet and
            is NOT what the real product labels "Cluster Target". */}
        <DataRow label="Cluster Target" value={fmt(card.target ?? card.pull_target)} color="var(--gold)" />
        {/* card.strength is now 100 x sum(|GEX_i| / chain_peak) over the cluster's
            strikes (core/scanner.py _cluster_strength), NOT weight_lab's abs_score.
            abs_score is a tier-offset sort key whose flooring pinned every triple near
            ~250; it correlated with the real product's Strength at only ~0.19. The
            normalized sum correlates ~0.35 live and shares the real magnitude range. */}
        <DataRow label="Strength" value={fmt(card.strength)} color={tone} />
      </div>

      {/* Prefer the cluster's own per-strike levels. Falling back to `setups` here
          rendered duplicates, because that list also carries the battle/golden/
          call_wall entries which frequently sit on the same strike as the cluster
          (SPY showed "100 · 775" twice plus two more at 775/770). */}
      <div className="mt-4 flex flex-row flex-wrap gap-1.5">
        {card.cluster?.levels?.length
          ? card.cluster.levels.map((lv) => (
              <ClusterLevelPill
                key={`lvl-${lv.strike}`}
                weight={lv.weight}
                strike={lv.strike}
                isAbove={card.cluster?.side === "above"}
                isTop={lv.strike === card.cluster?.target_strike}
              />
            ))
          : peaks.map((p, i) => (
              <ClusterPeakPill key={`${p.kind}-${p.center}`} setup={p} maxStrength={maxStrength} isTop={p.center === topStrike && i === 0} />
            ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Flash result card — a third distinct layout, confirmed against the real site: setup_type
// tag ("CEILING REJECTION #6"), a state badge, SPOT/PIVOT/FIRST TARGET/STRETCH/INVALIDATION/
// RUNWAY CLARITY stat rows, the Cipher Read narrative, and peak pills reused from the
// cluster card. Applies to flash / flash_index / flash_agentic strategies.
// ---------------------------------------------------------------------------

const AGENT_STATE_LABELS: Record<string, string> = {
  dormant: "Dormant",
  arming: "Arming",
  triggered: "Triggered",
  target_1_hit: "Target 1 Hit",
  target_2_hit: "Target 2 Hit",
  completed: "Completed",
};

function FlashResultCard({ card, rank }: { card: RealScanCard; rank: number }) {
  const isBullish = card.direction === "BULLISH";
  const tone = isBullish ? "var(--accent)" : "var(--neg)";
  const flash = card.flash;

  // Matches core/scanner.py's analyze_ticker(): runway clarity is a proxy from ATR quality
  // (60% weight) + thin-path/vacuum count (40%), not a field the API returns directly.
  const clarity = flash
    ? Math.max(0, Math.min(1, 0.55 * (flash.components?.atr ?? 0) + 0.45 * Math.min((card.vacuum_count ?? 0) / 3, 1)))
    : null;

  const peaks = (card.setups && card.setups.length > 0 ? card.setups : card.cluster ? [card.cluster] : [])
    .slice()
    .sort((a, b) => b.strength - a.strength)
    .slice(0, 5);
  const maxStrength = Math.max(...peaks.map((p) => p.strength), 1);
  const topStrike = peaks[0]?.center;

  return (
    <div
      className="flex flex-col rounded-[12px] overflow-hidden"
      style={{
        border: `1px solid color-mix(in srgb, ${tone} 45%, var(--line))`,
        background: `radial-gradient(130% 100% at 0% 0%, color-mix(in srgb, ${tone} 14%, transparent), var(--panel) 55%)`,
        padding: "18px 20px",
      }}
    >
      <div className="flex flex-row items-start justify-between gap-2">
        <div className="flex flex-row items-center gap-2 min-w-0 flex-wrap">
          <span className="text-[13px] font-semibold" style={{ color: "var(--text-mute)" }}>
            #{rank}
          </span>
          <span className="text-[16px] font-bold" style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
            ${card.ticker}
          </span>
          <span
            className="text-[10px] font-bold px-[10px] py-[3px] rounded-full uppercase shrink-0"
            style={{ border: `1px solid color-mix(in srgb, ${tone} 60%, transparent)`, color: tone, letterSpacing: "0.06em" }}
          >
            {isBullish ? "Bullish" : "Bearish"}
          </span>
        </div>
        <div className="text-right shrink-0">
          <span className="text-[22px] font-bold" style={{ color: "var(--text)" }}>
            {Math.round(card.score)}
          </span>
          <span className="text-[12px] font-semibold" style={{ color: "var(--text-mute)" }}>
            /100
          </span>
        </div>
      </div>

      <div className="flex flex-row flex-wrap items-center gap-1.5 mt-2">
        {card.setup_type && (
          <span
            className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
            style={{ border: `1px solid color-mix(in srgb, ${tone} 55%, transparent)`, color: tone }}
          >
            {card.setup_type}
          </span>
        )}
        {card.state && (
          <span
            className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
          >
            {AGENT_STATE_LABELS[card.state.toLowerCase()] ?? card.state}
          </span>
        )}
      </div>

      {/* Momentum-structure tags from the Obsidian EOD detector. */}
      {flash && (flash.setup || flash.coiling) && (
        <div className="flex flex-row flex-wrap items-center gap-1.5 mt-2">
          {flash.setup && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
              style={{ border: `1px solid color-mix(in srgb, ${tone} 55%, transparent)`, color: tone }}
            >
              {flash.setup}
            </span>
          )}
          {flash.edge_local != null && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
              title="Local conviction composite from the Obsidian momentum detector. This is NOT the real product's Edge score, whose formula has not been recovered."
            >
              Edge* {Math.round(flash.edge_local)}
            </span>
          )}
          {flash.coiling && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
              style={{ background: "color-mix(in srgb, var(--gold) 20%, transparent)", border: "1px solid var(--line)", color: "var(--gold)" }}
            >
              Coiling
            </span>
          )}
          {flash.eod_hot && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
              style={{ background: "color-mix(in srgb, var(--neg) 22%, transparent)", border: "1px solid var(--line)", color: "var(--neg)" }}
            >
              EOD Hot
            </span>
          )}
        </div>
      )}

      {flash && (flash.regime || flash.vwap_side || flash.dte != null) && (
        <div className="flex flex-row flex-wrap items-center gap-1.5 mt-2">
          {flash.regime && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
              style={{
                background: flash.regime === "pin" ? "color-mix(in srgb, var(--accent) 22%, transparent)" : "var(--panel-2)",
                border: "1px solid var(--line)",
                color:
                  flash.regime === "pin"
                    ? "var(--accent)"
                    : flash.regime === "trend"
                      ? "var(--gold)"
                      : "var(--text-dim)",
              }}
            >
              {flash.regime === "pin" ? "Pin" : flash.regime === "trend" ? "Trend" : "Mixed"}
            </span>
          )}
          {flash.vwap_side && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
            >
              VWAP {flash.vwap_side === "above" ? "▲" : "▼"}
            </span>
          )}
          {flash.dte != null && (
            <span
              className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
            >
              {flash.dte} DTE
            </span>
          )}
        </div>
      )}

      <div className="mt-3 pt-1" style={{ borderTop: "1px solid var(--line-soft)" }}>
        <DataRow label="Spot" value={fmt(card.spot)} color="var(--text)" />
        {flash && (
          <DataRow
            label="Pivot"
            value={`${fmt(flash.trigger)}${flash.trigger_kind ? ` (${flash.trigger_kind}${flash.trigger_proximity ? `, ${flash.trigger_proximity}` : ""})` : ""}`}
            color="var(--gold)"
          />
        )}
        {flash && <DataRow label="First Target" value={fmt(flash.first_target)} color={tone} />}
        {flash && (
          <DataRow
            label="Stretch"
            value={`${fmt(flash.stretch)}${flash.stretch_kind ? ` (${flash.stretch_kind} to watch)` : ""}`}
            color={tone}
          />
        )}
        <DataRow label="Invalidation" value={fmt(card.invalidation)} color="var(--neg)" />
        {clarity != null && <DataRow label="Runway Clarity" value={`${Math.round(clarity * 100)}%`} color="var(--text)" />}
      </div>

      {flash?.event_timeline && flash.event_timeline.length > 0 && (
        <div className="mt-3 flex flex-col gap-1">
          {flash.event_timeline.map((e, i) => (
            <div key={`${e.age}-${i}`} className="flex flex-row gap-2 text-[12px]">
              <span className="shrink-0 w-[42px] text-right" style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}>
                {e.age}
              </span>
              <span style={{ color: "var(--text-dim)" }}>{e.event}</span>
            </div>
          ))}
        </div>
      )}

      {(card.read || card.reason) && (
        <div className="mt-4">
          <div className="text-[11px] font-semibold uppercase mb-2" style={{ letterSpacing: "0.1em", color: "var(--text-mute)" }}>
            Cipher Read
          </div>
          <p className="text-[13.5px]" style={{ color: "var(--text-dim)", lineHeight: 1.5 }}>
            {card.read || card.reason}
          </p>
        </div>
      )}

      {peaks.length > 0 && (
        <div className="mt-4 flex flex-row flex-wrap gap-1.5">
          {peaks.map((p, i) => (
            <ClusterPeakPill key={`${p.kind}-${p.center}`} setup={p} maxStrength={maxStrength} isTop={p.center === topStrike && i === 0} />
          ))}
        </div>
      )}
    </div>
  );
}

type ClusterSideFilter = "all" | "upside" | "downside";
type ClusterKindFilter = "all" | "triple" | "quad";

// ---------------------------------------------------------------------------
// Flash Agentic live card — mirrors the real product's continuously-updating panel:
// named setup pattern + edge score + regime tag, a progress-to-target bar, level
// rows, and a chronological event timeline. Data comes from the real site's own
// panel via core/flash_agentic_live_loop.py, so these are its numbers, not ours.
// ---------------------------------------------------------------------------

function FlashAgenticCard({ row }: { row: FlashAgenticRow }) {
  const isBullish = row.bias === "BULLISH";
  const tone = isBullish ? "var(--accent)" : "var(--neg)";
  const pctMatch = /(\d+)%/.exec(row.target_progress || "");
  const progressPct = pctMatch ? Math.max(0, Math.min(100, Number(pctMatch[1]))) : 0;

  return (
    <div
      className="flex flex-col rounded-[12px] overflow-hidden"
      style={{
        border: `1px solid color-mix(in srgb, ${tone} 45%, var(--line))`,
        background: `radial-gradient(130% 100% at 0% 0%, color-mix(in srgb, ${tone} 14%, transparent), var(--panel) 55%)`,
        padding: "18px 20px",
      }}
    >
      <div className="flex flex-row items-start justify-between gap-2">
        <div className="flex flex-row items-center gap-2 min-w-0 flex-wrap">
          {row.state && (
            <span className="flex flex-row items-center gap-1.5 text-[10px] font-bold uppercase" style={{ color: tone }}>
              <span className="w-[7px] h-[7px] rounded-full animate-pulse" style={{ background: tone }} aria-hidden="true" />
              {row.state}
            </span>
          )}
          <span className="text-[16px] font-bold" style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
            ${row.ticker}
          </span>
          <span
            className="text-[10px] font-bold px-[10px] py-[3px] rounded-full uppercase shrink-0"
            style={{ border: `1px solid color-mix(in srgb, ${tone} 60%, transparent)`, color: tone, letterSpacing: "0.06em" }}
          >
            {isBullish ? "Bullish" : "Bearish"}
          </span>
        </div>
        {row.score != null && (
          <div className="text-right shrink-0">
            <span className="text-[22px] font-bold" style={{ color: "var(--text)" }}>
              {Math.round(row.score)}
            </span>
            <span className="text-[12px] font-semibold" style={{ color: "var(--text-mute)" }}>
              /100
            </span>
          </div>
        )}
      </div>

      <div className="flex flex-row flex-wrap items-center gap-1.5 mt-2">
        {row.setup && (
          <span
            className="text-[10px] font-bold px-[8px] py-[2px] rounded-full uppercase"
            style={{ border: `1px solid color-mix(in srgb, ${tone} 55%, transparent)`, color: tone }}
          >
            {row.setup}
          </span>
        )}
        {row.edge != null && (
          <span
            className="text-[10px] font-bold px-[8px] py-[2px] rounded-full"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
          >
            Edge {Math.round(row.edge)}
          </span>
        )}
        {row.regime && (
          <span
            className="text-[10px] font-bold px-[8px] py-[2px] rounded-full"
            style={{
              background: row.regime.toLowerCase() === "pin" ? "color-mix(in srgb, var(--accent) 22%, transparent)" : "var(--panel-2)",
              border: "1px solid var(--line)",
              color: row.regime.toLowerCase() === "pin" ? "var(--accent)" : "var(--gold)",
            }}
          >
            {row.regime}
          </span>
        )}
      </div>

      {row.target_progress && (
        <div className="mt-3">
          <div className="h-[10px] rounded-full overflow-hidden" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
            <div className="h-full rounded-full" style={{ width: `${progressPct}%`, background: tone }} />
          </div>
          <div className="text-[11px] mt-1 text-center" style={{ color: "var(--text-mute)" }}>
            {row.target_progress}
          </div>
        </div>
      )}

      <div className="mt-3 pt-1" style={{ borderTop: "1px solid var(--line-soft)" }}>
        {row.spot != null && <DataRow label="Spot" value={fmt(row.spot)} color="var(--text)" />}
        {row.pivot != null && <DataRow label="Pivot" value={fmt(row.pivot)} color="var(--gold)" />}
        {row.first_target != null && <DataRow label="Target" value={fmt(row.first_target)} color={tone} />}
        {row.stretch != null && <DataRow label="Stretch" value={fmt(row.stretch)} color={tone} />}
        {row.invalidation != null && <DataRow label="Invalidation" value={fmt(row.invalidation)} color="var(--neg)" />}
      </div>

      {row.event_timeline?.length > 0 && (
        <div className="mt-3 flex flex-col gap-1">
          {row.event_timeline.map((e, i) => (
            <div key={`${e.age}-${i}`} className="flex flex-row gap-2 text-[12px]">
              <span className="shrink-0 w-[42px] text-right" style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}>
                {e.age}
              </span>
              <span style={{ color: "var(--text-dim)" }}>{e.event}</span>
            </div>
          ))}
        </div>
      )}

      {row.cipher_read && (
        <div className="mt-4">
          <div className="text-[11px] font-semibold uppercase mb-2" style={{ letterSpacing: "0.1em", color: "var(--text-mute)" }}>
            Cipher Read
          </div>
          <p className="text-[13.5px]" style={{ color: "var(--text-dim)", lineHeight: 1.5 }}>
            {row.cipher_read}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SetupScanner() {
  const [mode, setMode] = useState<ScanMode>("short");
  const [clusterExp, setClusterExp] = useState(CLUSTER_EXP_OPTIONS[0].value);
  const [scanning, setScanning] = useState(false);
  const [hasResults, setHasResults] = useState(false);
  const [results, setResults] = useState<ScannerResultCard[]>([]);
  const [rawResults, setRawResults] = useState<RealScanCard[]>([]);
  const [lastStrategy, setLastStrategy] = useState<ScanStrategy | null>(null);
  const [sideFilter, setSideFilter] = useState<ClusterSideFilter>("all");
  const [kindFilter, setKindFilter] = useState<ClusterKindFilter>("all");
  const [job, setJob] = useState<RealScanJob | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyEntries, setHistoryEntries] = useState<SavedScanEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [agenticView, setAgenticView] = useState(false);
  const [agentic, setAgentic] = useState<FlashAgenticLive | null>(null);
  const [agenticError, setAgenticError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Flash Agentic is a continuously-refreshing live view (matching the real product),
  // not a one-shot scan — poll while it's the active view.
  useEffect(() => {
    if (!agenticView) return;
    const controller = new AbortController();
    let cancelled = false;
    const tick = async () => {
      try {
        const live = await fetchFlashAgenticLive(controller.signal);
        if (!cancelled) {
          setAgentic(live);
          setAgenticError("");
        }
      } catch (err) {
        if (!cancelled && !controller.signal.aborted) {
          setAgenticError(err instanceof ApiError ? err.message : "Failed to load Flash Agentic feed.");
        }
      }
    };
    tick();
    const interval = setInterval(tick, AGENTIC_POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [agenticView]);

  async function startScan(strategy: ScanStrategy) {
    if (scanning) return;
    if (pollRef.current) clearInterval(pollRef.current);
    setAgenticView(false);
    setHasResults(false);
    setResults([]);
    setRawResults([]);
    setSideFilter("all");
    setKindFilter("all");
    setJob(null);
    setErrorMessage("");
    setScanning(true);

    try {
      const { job_id } = await startScanJob({
        mode,
        strategy,
        limit: RESULT_LIMIT,
        clusterExp: strategy === "cipher" || strategy === "cluster" ? clusterExp : undefined,
      });

      pollRef.current = setInterval(async () => {
        try {
          const j = await fetchScanJob(job_id);
          setJob(j);
          if (j.status === "done") {
            if (pollRef.current) clearInterval(pollRef.current);
            const top = j.result?.top ?? [];
            const resolvedStrategy = (j.result?.strategy as ScanStrategy) ?? strategy;
            setLastStrategy(resolvedStrategy);
            if (resolvedStrategy === "cluster" || FLASH_STRATEGIES.has(resolvedStrategy)) {
              setRawResults(top);
            } else {
              setResults(top.map((c, i) => toCard(c, i + 1)));
            }
            setScanning(false);
            setHasResults(true);
          } else if (j.status === "error") {
            if (pollRef.current) clearInterval(pollRef.current);
            setErrorMessage(j.error || "Scan failed.");
            setScanning(false);
          } else if (j.partial_top?.length) {
            // Render the running leaderboard while the scan is still going. A
            // full-universe scan takes minutes; showing only a progress bar for that
            // long wastes results the engine has already produced.
            setLastStrategy(strategy);
            if (strategy === "cluster" || FLASH_STRATEGIES.has(strategy)) {
              setRawResults(j.partial_top);
            } else {
              setResults(j.partial_top.map((c, i) => toCard(c, i + 1)));
            }
            setHasResults(true);
          }
        } catch (err) {
          if (pollRef.current) clearInterval(pollRef.current);
          setErrorMessage(err instanceof ApiError ? err.message : "Lost connection while scanning.");
          setScanning(false);
        }
      }, POLL_MS);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Failed to start scan.");
      setScanning(false);
    }
  }

  async function toggleHistory() {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next) {
      setHistoryLoading(true);
      try {
        const { scans } = await listScanHistory({ limit: 30 });
        setHistoryEntries(scans);
      } catch {
        setHistoryEntries([]);
      } finally {
        setHistoryLoading(false);
      }
    }
  }

  async function loadFromHistory(entry: SavedScanEntry) {
    if (scanning) return;
    setErrorMessage("");
    try {
      const result = await loadSavedScan(entry.id);
      const top = result.top ?? [];
      const strategy = (result.strategy as ScanStrategy) ?? "cipher";
      setLastStrategy(strategy);
      if (strategy === "cluster" || FLASH_STRATEGIES.has(strategy)) {
        setRawResults(top);
        setResults([]);
      } else {
        setResults(top.map((c, i) => toCard(c, i + 1)));
        setRawResults([]);
      }
      setHasResults(true);
      setHistoryOpen(false);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Failed to load saved scan.");
    }
  }

  const pct = job?.pct ?? 0;
  const done = job?.done ?? 0;
  const total = job?.total ?? 0;
  const isClusterView = lastStrategy === "cluster";
  const isFlashView = lastStrategy != null && FLASH_STRATEGIES.has(lastStrategy);
  const isRawView = isClusterView || isFlashView;

  // The Upside/Downside/Triple/Quad filter row is cluster-only (confirmed against the real
  // site — flash-family results show no such filter); flash view passes everything through.
  const filteredRawResults = useMemo(() => {
    if (!isClusterView) return rawResults;
    return rawResults.filter((c) => {
      if (!c.cluster) return false;
      if (sideFilter === "upside" && c.cluster.side !== "above") return false;
      if (sideFilter === "downside" && c.cluster.side !== "below") return false;
      if (kindFilter !== "all" && c.cluster.kind !== kindFilter) return false;
      return true;
    });
  }, [rawResults, isClusterView, sideFilter, kindFilter]);

  return (
    <section
      className="setup-scanner flex flex-col gap-3"
      style={{ fontFamily: "var(--font-sans)", color: "var(--text)" }}
    >
      {/* Heading */}
      <div>
        <h1 className="text-[22px] font-bold" style={{ color: "var(--text)" }}>
          Setup Scanner
        </h1>
        <p className="text-[13px] mt-1 max-w-[640px]" style={{ color: "var(--text-dim)" }}>
          The Cipher engine ranks the entire universe and surfaces the 30 highest-conviction
          setups. Powered by the Cipher proprietary model.
        </p>
      </div>

      {/* Control row 1: mode tabs + primary CTA */}
      <div className="flex flex-row flex-wrap items-center gap-3 pt-1">
        <PillGroup options={MODE_OPTIONS} value={mode} onChange={setMode} disabled={scanning} />
        <button
          type="button"
          onClick={() => startScan("cipher")}
          disabled={scanning}
          className="rounded-[8px] px-[18px] py-[9px] text-[13px] font-bold whitespace-nowrap transition-opacity duration-150 disabled:opacity-80 disabled:cursor-not-allowed"
          style={{ background: "var(--accent)", color: "#f8f2ff" }}
        >
          {scanning ? "Scanning…" : "Cipher Model Scan"}
        </button>
      </div>

      {/* Control row 2: Liq scan / Cluster scan + cluster exp select */}
      <div className="flex flex-row flex-wrap items-center gap-3">
        <OutlineButton onClick={() => startScan("liquidity")} disabled={scanning}>
          Liq scan
        </OutlineButton>
        <OutlineButton onClick={() => startScan("cluster")} disabled={scanning}>
          Cluster scan
        </OutlineButton>
        <span
          className="text-[10.5px] font-semibold uppercase shrink-0"
          style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}
        >
          Cluster Exp
        </span>
        <div className="relative shrink-0">
          <select
            value={clusterExp}
            onChange={(e) => setClusterExp(e.target.value)}
            disabled={scanning}
            className="appearance-none rounded-[8px] pl-[12px] pr-[30px] py-[9px] text-[12.5px] font-semibold cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
          >
            {CLUSTER_EXP_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <svg
            viewBox="0 0 24 24"
            width="12"
            height="12"
            className="absolute right-[10px] top-1/2 -translate-y-1/2 pointer-events-none"
            style={{ color: "var(--text-mute)" }}
          >
            <path fill="currentColor" d="M7 10l5 5 5-5z" />
          </svg>
        </div>
      </div>

      {/* Control row 3: Flash family */}
      <div className="flex flex-row flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => startScan("flash")}
          disabled={scanning}
          className="flex flex-row items-center gap-2 rounded-[8px] px-[14px] py-[9px] text-[12.5px] font-semibold whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
          style={{
            border: "1px solid color-mix(in srgb, var(--gold) 55%, transparent)",
            color: "var(--gold)",
            background: "color-mix(in srgb, var(--gold) 6%, transparent)",
          }}
        >
          Flash
          <BetaBadge />
        </button>
        <button
          type="button"
          onClick={() => startScan("flash_index")}
          disabled={scanning}
          className="flex flex-row items-center gap-2 rounded-[8px] px-[14px] py-[9px] text-[12.5px] font-semibold whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
          style={{
            border: "1px solid color-mix(in srgb, var(--gold) 55%, transparent)",
            color: "var(--gold)",
            background: "color-mix(in srgb, var(--gold) 6%, transparent)",
          }}
        >
          Flash Index
          <BetaBadge />
        </button>
        <button
          type="button"
          onClick={() => setAgenticView((v) => !v)}
          aria-pressed={agenticView}
          disabled={scanning}
          className="flex flex-row items-center gap-2 rounded-[8px] px-[14px] py-[9px] text-[12.5px] font-semibold whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
          style={{
            border: "1px solid color-mix(in srgb, var(--accent) 55%, transparent)",
            color: "var(--accent)",
            background: agenticView
              ? "color-mix(in srgb, var(--accent) 28%, transparent)"
              : "color-mix(in srgb, var(--accent) 8%, transparent)",
          }}
        >
          <span
            className="w-[7px] h-[7px] rounded-full shrink-0 animate-pulse"
            style={{ background: "var(--accent)" }}
            aria-hidden="true"
          />
          Flash Agentic
          <BetaBadge />
        </button>
      </div>

      {/* Download CSV + local scan history — history is our own addition since the real
          product doesn't persist scans server-side (see core/scan_history.py). */}
      <div className="flex flex-row flex-wrap items-center gap-2">
        {hasResults && (
          <button
            type="button"
            onClick={() => (isRawView ? downloadClusterCsv(rawResults) : downloadCsv(results))}
            className="flex flex-row items-center gap-2 rounded-[8px] px-[14px] py-[9px] text-[12.5px] font-semibold whitespace-nowrap"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
          >
            <DownloadIcon width={13} height={13} />
            Download .CSV
          </button>
        )}
        <div className="relative">
          <button
            type="button"
            onClick={toggleHistory}
            aria-pressed={historyOpen}
            className="flex flex-row items-center gap-2 rounded-[8px] px-[14px] py-[9px] text-[12.5px] font-semibold whitespace-nowrap"
            style={{
              background: historyOpen ? "var(--nav-active)" : "var(--panel-2)",
              border: "1px solid var(--line)",
              color: "var(--text)",
            }}
          >
            History
          </button>
          {historyOpen && (
            <div
              className="absolute left-0 mt-2 w-[420px] max-h-[360px] overflow-y-auto rounded-[10px] z-20"
              style={{ background: "var(--panel)", border: "1px solid var(--line)", boxShadow: "0 14px 38px rgba(0,0,0,0.6)" }}
            >
              {historyLoading && (
                <div className="px-4 py-3 text-[12.5px]" style={{ color: "var(--text-mute)" }}>
                  Loading saved scans…
                </div>
              )}
              {!historyLoading && historyEntries.length === 0 && (
                <div className="px-4 py-3 text-[12.5px]" style={{ color: "var(--text-mute)" }}>
                  No saved scans yet — run a scan to populate history.
                </div>
              )}
              {!historyLoading &&
                historyEntries.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => loadFromHistory(entry)}
                    className="flex flex-col w-full text-left px-4 py-2.5"
                    style={{ borderBottom: "1px solid var(--line-soft)" }}
                  >
                    <div className="flex flex-row items-center justify-between gap-2">
                      <span className="text-[12.5px] font-semibold uppercase" style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>
                        {entry.strategy} · {entry.mode}
                      </span>
                      <span className="text-[11px]" style={{ color: "var(--text-mute)" }}>
                        {new Date(entry.as_of).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                      </span>
                    </div>
                    <span className="text-[11.5px]" style={{ color: "var(--text-dim)" }}>
                      {entry.qualified}/{entry.universe_size} qualified
                      {entry.top_ticker ? ` · top: $${entry.top_ticker}` : ""}
                    </span>
                  </button>
                ))}
            </div>
          )}
        </div>
      </div>

      {/* Cluster-only filters — side (upside/downside) and tier (triple/quad) */}
      {hasResults && isClusterView && (
        <div className="flex flex-row flex-wrap items-center gap-3">
          <PillGroup
            options={[
              { label: "All", value: "all" as ClusterSideFilter },
              { label: "Upside", value: "upside" as ClusterSideFilter },
              { label: "Downside", value: "downside" as ClusterSideFilter },
            ]}
            value={sideFilter}
            onChange={setSideFilter}
          />
          <PillGroup
            options={[
              { label: "All", value: "all" as ClusterKindFilter },
              { label: "Triple", value: "triple" as ClusterKindFilter },
              { label: "Quad", value: "quad" as ClusterKindFilter },
            ]}
            value={kindFilter}
            onChange={setKindFilter}
          />
        </div>
      )}

      {/* Helper text */}
      <p className="text-[13px]" style={{ color: "var(--text-mute)" }}>
        {HELPER_TEXT[mode]}
      </p>

      {/* Warning banner — visible while scanning and after results land */}
      {(scanning || hasResults) && (
        <div
          className="flex flex-row items-start gap-3 rounded-[10px] px-4 py-3"
          style={{
            background: "color-mix(in srgb, var(--gold) 10%, transparent)",
            border: "1px solid color-mix(in srgb, var(--gold) 40%, transparent)",
          }}
        >
          <span className="text-[15px] shrink-0" style={{ color: "var(--gold)" }} aria-hidden="true">
            ⚠
          </span>
          <p className="text-[13px]" style={{ color: "var(--text-dim)" }}>
            Scans aren&apos;t saved server-side by the real product, but this local build
            auto-saves every completed scan to history — or{" "}
            <strong style={{ color: "var(--text)" }}>download the .CSV</strong> to export.
            A full-universe scan can take a few minutes; keep this screen open and check back
            when it finishes.
          </p>
        </div>
      )}

      {/* Progress bar */}
      {scanning && (
        <div className="flex flex-col gap-2">
          <div
            className="w-full rounded-full overflow-hidden"
            style={{ height: "4px", background: "var(--panel-2)" }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full transition-[width] duration-100 ease-linear"
              style={{ width: `${pct}%`, background: "var(--accent)" }}
            />
          </div>
          <p className="text-[12.5px]" style={{ color: "var(--text-mute)" }}>
            {job?.message || (total > 0 ? `Scanning the universe… ${done}/${total} tickers (${pct}%)` : "Queuing scan…")}
            {hasResults && (
              <span style={{ color: "var(--gold)" }}>
                {" "}
                · showing partial results, ranking may still change
              </span>
            )}
          </p>
        </div>
      )}

      {/* Error state */}
      {!scanning && errorMessage && (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <p className="text-[13px]" style={{ color: "var(--neg)" }}>
            {errorMessage}
          </p>
        </div>
      )}

      {/* Flash Agentic live view — a standing feed, not a one-shot scan */}
      {agenticView && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-row flex-wrap items-center justify-between gap-2">
            <div className="flex flex-row items-center gap-2">
              <span
                className="flex flex-row items-center gap-1.5 text-[11px] font-bold uppercase px-[10px] py-[4px] rounded-full"
                style={{
                  border: `1px solid color-mix(in srgb, ${agentic?.loop_running ? "var(--accent)" : "var(--text-mute)"} 55%, transparent)`,
                  color: agentic?.loop_running ? "var(--accent)" : "var(--text-mute)",
                }}
              >
                <span
                  className={`w-[7px] h-[7px] rounded-full ${agentic?.loop_running ? "animate-pulse" : ""}`}
                  style={{ background: agentic?.loop_running ? "var(--accent)" : "var(--text-mute)" }}
                  aria-hidden="true"
                />
                {agentic?.loop_running ? "Live" : "Idle"}
              </span>
              <span className="text-[15px] font-bold" style={{ color: "var(--text)" }}>
                Flash Agentic
              </span>
              <BetaBadge />
            </div>
            {agentic && (
              <span className="text-[11.5px]" style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}>
                {agentic.cycle != null ? `cycle ${agentic.cycle} · ` : ""}
                {agentic.captured_at
                  ? `captured ${new Date(agentic.captured_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" })}`
                  : "no capture yet"}
              </span>
            )}
          </div>

          {!agentic?.loop_running && (
            <div
              className="flex flex-row items-start gap-3 rounded-[10px] px-4 py-3"
              style={{
                background: "color-mix(in srgb, var(--gold) 10%, transparent)",
                border: "1px solid color-mix(in srgb, var(--gold) 40%, transparent)",
              }}
            >
              <span className="text-[15px] shrink-0" style={{ color: "var(--gold)" }} aria-hidden="true">
                ⚠
              </span>
              <p className="text-[13px]" style={{ color: "var(--text-dim)" }}>
                The background capture loop isn&apos;t running, so this shows the last captured
                snapshot. Start it with{" "}
                <code style={{ color: "var(--text)" }}>python3 core/flash_agentic_live_loop.py</code>{" "}
                — it drives a real browser session against the live product.
              </p>
            </div>
          )}

          {agenticError && (
            <p className="text-[13px]" style={{ color: "var(--neg)" }}>
              {agenticError}
            </p>
          )}

          {agentic && agentic.rows.length === 0 && !agenticError && (
            <div className="flex items-center justify-center py-16">
              <p className="text-[13px]" style={{ color: "var(--text-mute)" }}>
                No active Flash Agentic signals in the latest capture.
              </p>
            </div>
          )}

          {agentic && agentic.rows.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 mt-1">
              {agentic.rows.map((row) => (
                <FlashAgenticCard key={`${row.ticker}-${row.rank}`} row={row} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Results grid */}
      {!agenticView && hasResults && isClusterView && filteredRawResults.length === 0 && (
        <div className="flex items-center justify-center py-16">
          <p className="text-[13px]" style={{ color: "var(--text-mute)" }}>
            No clusters match the current filters.
          </p>
        </div>
      )}
      {!agenticView && hasResults && isRawView && filteredRawResults.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 mt-1">
          {filteredRawResults.map((card, i) =>
            isFlashView ? (
              <FlashResultCard key={card.ticker} card={card} rank={i + 1} />
            ) : (
              <ClusterResultCard key={card.ticker} card={card} rank={i + 1} />
            )
          )}
        </div>
      )}
      {!agenticView && hasResults && !isRawView && (
        <div className="grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 mt-1">
          {results.map((card) => (
            <ResultCard key={card.ticker} card={card} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!agenticView && !scanning && !hasResults && !errorMessage && (
        <div className="flex items-center justify-center py-20">
          <p className="text-[13px]" style={{ color: "var(--text-mute)" }}>
            Pick a timeframe and run a scan to surface the top 30 setups across the universe.
          </p>
        </div>
      )}
    </section>
  );
}

export default SetupScanner;
