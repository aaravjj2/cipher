"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchFlow, type RealFlowPrint } from "@/lib/api";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/**
 * TS overlay — the real product's FLOW TAPE.
 *
 * The "TS" header button previously rendered a "not yet implemented" chip, because
 * no capture had ever shown what it did. A read-only probe of the live app
 * (scripts/probe_ticker_edge_surface.py) dumped the panel: a premium-filtered tape
 * with tabs ">5k / >50k / >100k / SQ", a Live/date selector, and the columns
 * PREMIUM, STRIKE, C/P, SIDE, EXP, TIME, LIVE.
 *
 * Everything here is driven by our existing /api/flow (via fetchFlow), which
 * already returns premium, strike, type, side, expiration and time per print — so
 * the tape is real data, not a mock.
 *
 * Two parts of the real panel are deliberately NOT reproduced:
 *   - the historical date tabs (Aug 6, Aug 5, …), because /api/flow serves only
 *     the current session and there is no stored print history to back them;
 *   - the "SQ" tab, whose meaning has not been confirmed from any capture.
 * Both are surfaced as disabled controls with a reason, rather than as buttons
 * that silently return today's data under yesterday's label.
 */

const PREMIUM_TIERS = [
  { label: ">5k", value: 5_000 },
  { label: ">50k", value: 50_000 },
  { label: ">100k", value: 100_000 },
] as const;

const REFRESH_MS = 15_000;

function money(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
  return `$${Math.round(value)}`;
}

function clockTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-US", {
    hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function marketDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function expLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export default function FlowTape({ ticker }: { ticker: string }) {
  const [minPremium, setMinPremium] = useState<number>(50_000);
  const [prints, setPrints] = useState<RealFlowPrint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [sessionDate, setSessionDate] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [caveat, setCaveat] = useState<string | null>(null);
  const [availability, setAvailability] = useState<"available" | "refreshing" | "unavailable">("available");
  const [freshness, setFreshness] = useState<{ status: "current" | "stale" | "unknown"; age_seconds: number | null }>({ status: "unknown", age_seconds: null });
  const [live, setLive] = useState(true);
  // Tracks the newest print already seen so arriving rows can be highlighted
  // without re-animating the whole tape on every poll.
  const seenRef = useRef<Set<string>>(new Set());
  const [fresh, setFresh] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!ticker) return;
      setLoading(true);
      try {
        const res = await fetchFlow(ticker, { minPremium }, signal);
        const rows = [...(res.prints ?? [])].sort(
          (a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()
        );
        const arrived = new Set<string>();
        for (const p of rows) {
          const key = `${p.contract}|${p.time}|${p.premium}`;
          if (!seenRef.current.has(key)) arrived.add(key);
        }
        // First load is not "new" — everything would flash at once.
        seenRef.current = new Set(rows.map((p) => `${p.contract}|${p.time}|${p.premium}`));
        setFresh(prints.length === 0 ? new Set() : arrived);
        setPrints(rows);
        setAsOf(res.as_of ?? null);
        setSessionDate(res.session_date ?? null);
        setSource(res.source ?? null);
        setCaveat(res.caveat ?? null);
        setAvailability(res.availability?.status ?? "available");
        setFreshness(res.freshness ?? { status: res.event_age_seconds == null ? "unknown" : res.event_age_seconds <= 120 ? "current" : "stale", age_seconds: res.event_age_seconds ?? null });
        setError(null);
      } catch (err) {
        if ((err as Error)?.name !== "AbortError") {
          setError((err as Error)?.message || "flow unavailable");
        }
      } finally {
        setLoading(false);
      }
    },
    // `prints.length` only gates the first-load highlight suppression above.
    [ticker, minPremium, prints.length]
  );

  useEffect(() => {
    seenRef.current = new Set();
    setPrints([]);
  }, [ticker, minPremium]);

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    if (!live) return () => ctrl.abort();
    const id = setInterval(() => void load(ctrl.signal), REFRESH_MS);
    return () => {
      ctrl.abort();
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, minPremium, live]);

  const totalPremium = prints.reduce((sum, p) => sum + (p.premium || 0), 0);
  const calls = prints.filter((p) => p.type === "call").length;

  return (
    <div
      className="mt-2 rounded-lg border text-[11px]"
      style={{ borderColor: "var(--border)", background: "var(--panel)" }}
    >
      <div
        className="flex flex-wrap items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="text-[11px] font-bold tracking-wide" style={{ color: "var(--text)" }}>
          FLOW TAPE
        </span>

        {PREMIUM_TIERS.map((tier) => (
          <button
            key={tier.label}
            type="button"
            onClick={() => setMinPremium(tier.value)}
            className="rounded px-2 py-0.5 text-[10px] font-semibold"
            style={{
              background: minPremium === tier.value ? "var(--nav-active)" : "transparent",
              color: minPremium === tier.value ? "var(--text)" : "var(--text-mute)",
              border: "1px solid var(--border)",
            }}
          >
            {tier.label}
          </button>
        ))}

        {/* Present in the real panel; meaning unconfirmed, so it is shown disabled
            rather than wired to a guess. */}
        <button
          type="button"
          disabled
          title="The real panel has an SQ tab; its filter semantics have not been confirmed from any capture, so it is not wired up."
          className="cursor-not-allowed rounded px-2 py-0.5 text-[10px] font-semibold opacity-40"
          style={{ color: "var(--text-mute)", border: "1px solid var(--border)" }}
        >
          SQ
        </button>

        <span className="mx-1 h-3 w-px" style={{ background: "var(--border)" }} />

        <button
          type="button"
          onClick={() => setLive((v) => !v)}
          className="rounded px-2 py-0.5 text-[10px] font-semibold"
          style={{
            background: live ? "var(--nav-active)" : "transparent",
            color: live ? "var(--text)" : "var(--text-mute)",
            border: "1px solid var(--border)",
          }}
        >
          {live ? "● Auto refresh" : "Paused"}
        </button>

        <span
          className="text-[10px]"
          title="The real panel offers previous session dates. /api/flow serves only the current session and no print history is stored, so those tabs would return today's tape under another day's label."
          style={{ color: "var(--text-mute)", opacity: 0.6 }}
        >
          {sessionDate ? `session ${sessionDate}` : "session unavailable"}
        </span>

        <span className="ml-auto text-[10px]" style={{ color: "var(--text-mute)" }}>
          {loading && prints.length === 0
            ? "loading…"
            : `${prints.length} prints · ${money(totalPremium)} · ${calls}C/${prints.length - calls}P`}
          {asOf ? ` · newest ${marketDateTime(asOf)} ET` : ""}
        </span>
        <span className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase" style={{ color: freshness.status === "current" ? "var(--accent)" : freshness.status === "stale" ? "var(--gold)" : "var(--text-mute)", border: "1px solid var(--border)" }} title="Freshness is based on the newest represented event, not the time the browser fetched the response.">
          {freshness.status}{freshness.age_seconds != null ? ` · ${Math.round(freshness.age_seconds)}s` : ""}
        </span>
      </div>

      <div
        className="border-b px-3 py-1.5 text-[10px]"
        style={{ borderColor: "var(--border)", color: "var(--text-mute)" }}
        title={caveat ?? undefined}
      >
        {source === "tradier_stream"
          ? "Captured event timesales"
          : source === "alpaca_chain_snapshot"
            ? "Chain snapshot fallback"
            : availability === "refreshing"
              ? "Provider refresh pending"
              : "Flow unavailable"}
        {caveat ? ` · ${caveat}` : ""}
      </div>

      {error && (
        <div className="px-3 py-2 text-[10px]" style={{ color: "var(--neg)" }}>
          {error}
        </div>
      )}

      <div className="max-h-[280px] overflow-y-auto overflow-x-auto">
        <table className="w-full border-collapse text-left tabular-nums">
          <thead className="sticky top-0" style={{ background: "var(--panel)" }}>
            <tr style={{ color: "var(--text-mute)" }}>
              {["PREMIUM", "STRIKE", "C/P", "SIDE", "EXP", "TIME"].map((h) => (
                <th
                  key={h}
                  className="whitespace-nowrap border-b px-3 py-1 text-[9px] font-semibold tracking-wide"
                  style={{ borderColor: "var(--border)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {prints.length === 0 && loading && (
              <tr aria-label={`Loading flow prints for ${ticker}`}>
                <td colSpan={6} className="px-3 py-3">
                  <SkeletonRegion label={`Loading flow prints for ${ticker}…`}>
                    {["w-[72%]", "w-[54%]", "w-[64%]", "w-[44%]"].map((width) => (
                      <Skeleton key={width} className={`h-[16px] ${width}`} />
                    ))}
                  </SkeletonRegion>
                </td>
              </tr>
            )}
            {prints.length === 0 && !loading && !error && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-center text-[10px]" style={{ color: "var(--text-mute)" }}>
                  No prints above {money(minPremium)} for {ticker} in the captured session. Lower the premium threshold or keep auto refresh on.
                </td>
              </tr>
            )}
            {prints.map((p) => {
              const key = `${p.contract}|${p.time}|${p.premium}`;
              const isCall = p.type === "call";
              return (
                <tr
                  key={key}
                  style={{
                    background: fresh.has(key) ? "var(--nav-active)" : "transparent",
                    color: "var(--text-dim)",
                  }}
                >
                  <td className="whitespace-nowrap px-3 py-1 font-semibold" style={{ color: "var(--text)" }}>
                    {money(p.premium)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-1">{p.strike}</td>
                  <td
                    className="whitespace-nowrap px-3 py-1 font-semibold"
                    style={{ color: isCall ? "var(--positive)" : "var(--negative)" }}
                  >
                    {isCall ? "C" : "P"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-1 uppercase">{p.side === "unknown" ? "—" : p.side}</td>
                  <td className="whitespace-nowrap px-3 py-1">{expLabel(p.expiration)}</td>
                  <td className="whitespace-nowrap px-3 py-1" title={marketDateTime(p.time)}>{clockTime(p.time)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
