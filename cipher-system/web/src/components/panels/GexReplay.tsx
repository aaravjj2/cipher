"use client";

import { useEffect, useMemo, useState } from "react";
import {
  fetchGexReplayCatalog,
  fetchGexReplaySnapshot,
  type GexReplayCatalog,
  type GexReplayPayload,
} from "@/lib/api";
import { formatDollar, getCellColor } from "@/components/panels/HeatmapGrid";

const PLAY_MS = 1200;

function stamp(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function metric(value: number | null, dollars = false): string {
  if (value == null) return "unknown";
  return dollars ? formatDollar(value) : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function GexReplay({ ticker }: { ticker: string }) {
  const [catalog, setCatalog] = useState<GexReplayCatalog | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [payload, setPayload] = useState<GexReplayPayload | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchGexReplayCatalog(ticker, controller.signal)
      .then((value) => {
        setCatalog(value);
        setSelectedId(value.snapshots.at(-1)?.id ?? null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Could not load GEX history.");
      });
    return () => controller.abort();
  }, [ticker]);

  useEffect(() => {
    if (selectedId == null) return;
    const controller = new AbortController();
    fetchGexReplaySnapshot(selectedId, controller.signal)
      .then(setPayload)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Could not load snapshot.");
      });
    return () => controller.abort();
  }, [selectedId]);

  const timeline = useMemo(() => [...(catalog?.snapshots ?? [])].reverse(), [catalog]);
  const index = Math.max(0, timeline.findIndex((row) => row.id === selectedId));
  useEffect(() => {
    if (!playing || timeline.length < 2) return;
    const timer = window.setInterval(() => {
      setSelectedId((current) => {
        const at = Math.max(0, timeline.findIndex((row) => row.id === current));
        if (at >= timeline.length - 1) { setPlaying(false); return current; }
        return timeline[at + 1].id;
      });
    }, PLAY_MS);
    return () => window.clearInterval(timer);
  }, [playing, timeline]);

  const maxAbs = Math.max(1, ...(payload?.strikes.map((row) => Math.abs(row.net_gex ?? 0)) ?? [1]));
  const nearest = payload?.snapshot.spot == null ? null : payload.strikes.reduce((best, row) =>
    Math.abs(row.strike - payload.snapshot.spot!) < Math.abs(best.strike - payload.snapshot.spot!) ? row : best,
    payload.strikes[0]
  );

  if (error) return <p className="text-[12px]" style={{ color: "var(--neg)" }}>{error}</p>;
  if (!catalog) return <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>Loading captured GEX history…</p>;
  if (!timeline.length) return <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>No captured GEX snapshots for {ticker}.</p>;

  return (
    <div className="flex flex-col gap-4" style={{ color: "var(--text)" }}>
      <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><h2 className="text-[15px] font-semibold">{ticker} captured GEX replay</h2><p className="mt-1 text-[11px]" style={{ color: "var(--text-dim)" }}>{timeline.length} captured profiles · {catalog.counts.tickers} tracked tickers · read only</p></div>
          <div className="flex gap-2">
            <button type="button" disabled={index === 0} onClick={() => setSelectedId(timeline[index - 1]?.id ?? selectedId)} className="rounded px-3 py-1 text-[11px] disabled:opacity-40" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>Previous</button>
            <button type="button" onClick={() => setPlaying((value) => !value)} className="rounded px-3 py-1 text-[11px]" style={{ background: playing ? "var(--nav-active)" : "var(--panel-2)", border: "1px solid var(--accent)" }}>{playing ? "Pause" : "Play"}</button>
            <button type="button" disabled={index >= timeline.length - 1} onClick={() => setSelectedId(timeline[index + 1]?.id ?? selectedId)} className="rounded px-3 py-1 text-[11px] disabled:opacity-40" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>Next</button>
          </div>
        </div>
        <input aria-label="GEX replay timeline" className="mt-4 w-full accent-[var(--accent)]" type="range" min={0} max={timeline.length - 1} value={index} onChange={(event) => { setPlaying(false); setSelectedId(timeline[Number(event.target.value)].id); }} />
        <div className="mt-1 flex justify-between text-[9.5px]" style={{ color: "var(--text-mute)" }}><span>{stamp(timeline[0].captured_at)}</span><span>{payload ? stamp(payload.snapshot.captured_at) : "Loading…"}</span><span>{stamp(timeline.at(-1)!.captured_at)}</span></div>
        <p className="mt-3 text-[10.5px]" style={{ color: "var(--text-mute)" }}>{catalog.caveat} Missing gamma or open interest remains unknown.</p>
      </section>

      {payload && <>
        <section className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {[["Spot", payload.snapshot.spot], ["Call wall", payload.snapshot.call_wall_strike], ["Put wall", payload.snapshot.put_wall_strike], ["Gamma flip", payload.snapshot.gamma_flip_level], ["Global max", payload.snapshot.global_max_strike], ["Contracts", payload.snapshot.contracts]].map(([label, value]) =>
            <div key={String(label)} className="rounded-[8px] p-3" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><div className="text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>{label}</div><div className="mt-1 font-mono text-[13px]">{metric(value as number | null)}</div></div>
          )}
        </section>
        <section className="overflow-hidden rounded-[var(--radius)]" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
          <div className="grid grid-cols-[90px_repeat(3,minmax(110px,1fr))_90px] gap-px px-3 py-2 text-[9px] font-semibold uppercase" style={{ background: "var(--panel-2)", color: "var(--text-mute)" }}><span>Strike</span><span>Call GEX</span><span>Put GEX</span><span>Net GEX</span><span>Coverage</span></div>
          <div className="max-h-[620px] overflow-y-auto">
            {payload.strikes.map((row) => <div key={row.strike} className="grid grid-cols-[90px_repeat(3,minmax(110px,1fr))_90px] items-center gap-px border-t px-3 py-1 font-mono text-[10.5px]" style={{ borderColor: "var(--line)", background: nearest?.strike === row.strike ? "color-mix(in srgb, var(--gold) 9%, transparent)" : undefined }}>
              <span style={{ color: nearest?.strike === row.strike ? "var(--gold)" : "var(--text)" }}>{row.strike.toFixed(2)}</span>
              {[row.call_gex, row.put_gex, row.net_gex].map((value, cell) => <span key={cell} className="rounded px-2 py-1 text-right" style={{ background: value == null ? "var(--panel-2)" : getCellColor(value, maxAbs), color: value == null ? "var(--text-mute)" : "white" }}>{value == null ? "unknown" : formatDollar(value)}</span>)}
              <span className="text-right" style={{ color: row.incomplete ? "var(--gold)" : "var(--text-dim)" }}>{row.available_cells}/{row.listed_cells}{row.incomplete ? "*" : ""}</span>
            </div>)}
          </div>
          <p className="border-t px-4 py-3 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>{payload.aggregation} * Incomplete means one or more captured expiration cells lacked gamma or open interest.</p>
        </section>
      </>}
    </div>
  );
}
