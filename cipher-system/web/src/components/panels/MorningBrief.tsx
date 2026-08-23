"use client";

import { useEffect, useState } from "react";
import {
  fetchAutopilotStatus,
  fetchMorningBrief,
  type AutopilotStatus,
  type MorningBriefResponse,
} from "@/lib/api";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

const money = (value?: number | null) =>
  value == null
    ? "—"
    : value.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      });

const pct = (value?: number | null) =>
  value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;

const when = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString("en-US", {
        timeZone: "America/New_York",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "—";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border p-4" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
      <h2 className="mb-3 text-[10px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-mute)" }}>
        {title}
      </h2>
      {children}
    </section>
  );
}

export function MorningBrief({ ticker, onNavigate }: {
  ticker: string;
  onNavigate?: (panel: string, ticker?: string) => void;
}) {
  const [data, setData] = useState<MorningBriefResponse | null>(null);
  const [autopilot, setAutopilot] = useState<AutopilotStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    Promise.all([
      fetchMorningBrief(ticker, ctrl.signal),
      fetchAutopilotStatus(ctrl.signal).catch(() => null),
    ])
      .then(([brief, status]) => {
        setData(brief);
        setAutopilot(status);
      })
      .catch((reason) => {
        if (!ctrl.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Morning Brief unavailable");
        }
      });
    return () => ctrl.abort();
  }, [ticker]);

  if (error) return <div style={{ color: "var(--negative)" }}>{error}</div>;
  if (!data) {
    return (
      <SkeletonRegion label="Building Morning Brief…">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-44 w-full" />
      </SkeletonRegion>
    );
  }

  const prospective = data.prospective_fronttests;
  const flow = data.significant_flow.prints ?? [];
  const flowState = data.significant_flow.availability?.status ?? "available";
  const openPaper = data.paper_portfolios.portfolios.reduce(
    (sum, row) => sum + (row.open_positions ?? 0), 0,
  );
  const executorState = autopilot?.executor.operating_state;
  const executorOpen = (autopilot?.executor.counts.open_shadow_positions ?? 0) + (autopilot?.executor.counts.open_paper_positions ?? 0);
  const autopilotSummary = !executorState
    ? "Status unavailable"
    : executorState === "ACTIVE_POSITION"
      ? `${executorOpen} active simulated position${executorOpen === 1 ? "" : "s"}`
      : executorState === "DATA_FAILURE"
        ? "Data failure · entries blocked"
        : executorState === "SETUP_REJECTED"
          ? "Latest setup rejected by rules"
          : executorState === "HEALTHY_NO_SETUP"
            ? "Healthy · no setup"
            : "Waiting for first OPRA check";

  return (
    <div className="space-y-4" style={{ fontFamily: "var(--font-mono)" }}>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Morning Brief</h1>
          <p className="text-[11px]" style={{ color: "var(--text-mute)" }}>
            {data.session.phase.toUpperCase()} · {data.session.market_date} ET · {when(data.generated_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {["Setup Scanner", "Night Vision", "Options Terminal", "Paper Portfolios"].map((panel) => (
            <button key={panel} type="button" onClick={() => onNavigate?.(panel, ticker)} className="rounded-lg border px-3 py-1.5 text-[10px] hover:bg-white/[0.04]" style={{ borderColor: "var(--line)", color: "var(--accent)" }}>
              {panel.replace(" Terminal", "")}
            </button>
          ))}
        </div>
      </header>

      {data.attention.length > 0 ? (
        <section className="rounded-xl border p-3" style={{ borderColor: "var(--gold)", background: "var(--panel)" }}>
          <h2 className="text-[10px] font-bold uppercase" style={{ color: "var(--gold)" }}>
            Check first · {data.attention.length} issue{data.attention.length === 1 ? "" : "s"}
          </h2>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {data.attention.slice(0, 4).map((item, index) => (
              <div key={`${item.kind}-${index}`} className="text-[10px]">
                <b>{item.title}</b><span style={{ color: "var(--text-dim)" }}> · {item.detail}</span>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <p className="text-[10px]" style={{ color: "var(--positive)" }}>Inputs healthy · no data exceptions</p>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Market now">
          <div className="space-y-2">
            {data.market.map((row) => (
              <div key={row.ticker} className="flex items-center justify-between text-xs">
                <b>{row.ticker}</b>
                <span>{row.price == null ? "refreshing" : money(row.price)}</span>
                <span style={{ color: (row.day_change_pct ?? 0) >= 0 ? "var(--positive)" : "var(--negative)" }}>{pct(row.day_change_pct)}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card title={`Focus · ${ticker}`}>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div><span style={{ color: "var(--text-mute)" }}>Large flow</span><b className="mt-1 block">{flow.length} print{flow.length === 1 ? "" : "s"}</b></div>
            <div><span style={{ color: "var(--text-mute)" }}>Public-OI GEX change</span><b className="mt-1 block">{money(data.gex_change?.change)}</b></div>
          </div>
          {!flow.length && (
            <p className="mt-3 text-[10px]" style={{ color: "var(--text-mute)" }}>
              {flowState === "refreshing"
                ? "Flow refresh pending; unknown, not zero."
                : flowState === "unavailable"
                  ? "Flow unavailable; no observation inferred."
                  : "No captured prints above $100K."}
            </p>
          )}
          {flow[0] && <p className="mt-3 text-[10px]" style={{ color: "var(--text-dim)" }}>Largest: {flow[0].type.toUpperCase()} {flow[0].strike} · {money(flow[0].premium)} · {flow[0].side}</p>}
        </Card>

        <Card title="Paper status">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div><span style={{ color: "var(--text-mute)" }}>Open</span><b className="mt-1 block">{openPaper}</b></div>
            <div><span style={{ color: "var(--text-mute)" }}>Realized P&amp;L</span><b className="mt-1 block">{money(data.paper_portfolios.combined_realized_pnl)}</b></div>
            <div><span style={{ color: "var(--text-mute)" }}>Autopilot</span><b className="mt-1 block">{autopilotSummary}</b></div>
            <div><span style={{ color: "var(--text-mute)" }}>Open research</span><b className="mt-1 block">{prospective.open_signals.length}</b></div>
          </div>
          {executorState === "DATA_FAILURE" && <p className="mt-3 text-[10px]" style={{ color: "var(--negative)" }}>{autopilot?.executor.entry_blocked_reason?.replaceAll("_", " ") ?? "Provider unavailable"}. No simulated fill was created.</p>}
          <p className="mt-3 text-[9px]" style={{ color: "var(--text-mute)" }}>Paper simulation only · no broker-order capability.</p>
        </Card>
      </div>

      <Card title="Setups to review">
        <div className="grid gap-3 lg:grid-cols-3">
          <div>
            <h3 className="mb-2 text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Autopilot watch</h3>
            {(autopilot?.plan.candidates ?? []).slice(0, 5).map((row) => (
              <button key={row.ticker} type="button" onClick={() => onNavigate?.("Ticker Workbench", row.ticker)} className="block w-full rounded px-1 py-1 text-left text-[10px] hover:bg-white/[0.04]">
                <b>{row.ticker}</b> · {row.direction} · score {row.score} · R/R {row.reward_risk}
              </button>
            ))}
            {!autopilot?.plan.candidates.length && <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>No candidates yet.</p>}
          </div>
          <div>
            <h3 className="mb-2 text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Open observations</h3>
            {prospective.open_signals.slice(0, 5).map((row) => (
              <button key={row.signal_id} type="button" onClick={() => onNavigate?.("Ticker Workbench", row.ticker)} className="block w-full rounded px-1 py-1 text-left text-[10px] hover:bg-white/[0.04]">
                <b>{row.ticker}</b> · {row.direction.toUpperCase()} · {row.setup_id.replaceAll("_", " ")}
              </button>
            ))}
            {!prospective.open_signals.length && <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>None open.</p>}
          </div>
          <div>
            <h3 className="mb-2 text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>Latest scans</h3>
            {data.recent_scans.slice(0, 5).map((row) => (
              <button key={row.id} type="button" onClick={() => onNavigate?.("Setup Scanner", row.top_ticker ?? undefined)} className="block w-full rounded px-1 py-1 text-left text-[10px] hover:bg-white/[0.04]">
                <b>{row.top_ticker ?? "No leader"}</b> · {row.strategy} · {row.qualified ?? 0} qualified
              </button>
            ))}
            {!data.recent_scans.length && <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>No saved scans.</p>}
          </div>
        </div>
      </Card>
    </div>
  );
}
