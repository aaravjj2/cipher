"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchGexReplayCatalog,
  fetchGexReplaySnapshot,
  type GexReplayCatalog,
  type GexReplayPayload,
} from "@/lib/api";
import { formatDollar, getCellColor } from "@/components/panels/HeatmapGrid";
import { SkeletonCards, SkeletonGrid } from "@/components/ui/skeleton";

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
  const [catalogTicker, setCatalogTicker] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [payload, setPayload] = useState<GexReplayPayload | null>(null);
  const [loadedSnapshotId, setLoadedSnapshotId] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorTicker, setErrorTicker] = useState<string | null>(null);
  const [errorSnapshotId, setErrorSnapshotId] = useState<number | null>(null);
  const catalogRequest = useRef(0);
  const snapshotRequest = useRef(0);

  useEffect(() => {
    const requestId = ++catalogRequest.current;
    ++snapshotRequest.current;
    const controller = new AbortController();
    fetchGexReplayCatalog(ticker, controller.signal)
      .then((value) => {
        if (requestId !== catalogRequest.current) return;
        setCatalog(value);
        setCatalogTicker(ticker);
        setLoadedSnapshotId(null);
        setErrorSnapshotId(null);
        setSelectedId(value.snapshots.at(-1)?.id ?? null);
        setError(null);
        setErrorTicker(null);
        setErrorSnapshotId(null);
        setPlaying(false);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted && requestId === catalogRequest.current) {
          setError(err instanceof Error ? err.message : "Could not load GEX history.");
          setErrorTicker(ticker);
          setErrorSnapshotId(null);
        }
      });
    return () => {
      controller.abort();
    };
  }, [ticker]);

  useEffect(() => {
    const requestId = ++snapshotRequest.current;
    if (selectedId == null || catalogTicker !== ticker) return;
    const snapshotId = selectedId;
    const controller = new AbortController();
    fetchGexReplaySnapshot(snapshotId, controller.signal)
      .then((value) => {
        if (requestId !== snapshotRequest.current) return;
        setPayload(value);
        setLoadedSnapshotId(snapshotId);
        setError(null);
        setErrorTicker(null);
        setErrorSnapshotId(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted && requestId === snapshotRequest.current) {
          setError(err instanceof Error ? err.message : "Could not load snapshot.");
          setErrorTicker(ticker);
          setErrorSnapshotId(snapshotId);
        }
      });
    return () => {
      controller.abort();
    };
  }, [selectedId, catalogTicker, ticker]);

  const currentCatalog = catalogTicker === ticker ? catalog : null;
  const currentPayload = loadedSnapshotId === selectedId && catalogTicker === ticker ? payload : null;
  const timeline = useMemo(() => [...(currentCatalog?.snapshots ?? [])].reverse(), [currentCatalog]);
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

  const maxAbs = Math.max(1, ...(currentPayload?.strikes.map((row) => Math.abs(row.net_gex ?? 0)) ?? [1]));
  const nearest = currentPayload?.snapshot.spot == null || !currentPayload.strikes.length ? null : currentPayload.strikes.reduce((best, row) =>
    Math.abs(row.strike - currentPayload.snapshot.spot!) < Math.abs(best.strike - currentPayload.snapshot.spot!) ? row : best,
    currentPayload.strikes[0]
  );

  const currentError = errorTicker === ticker && (errorSnapshotId == null || errorSnapshotId === selectedId) ? error : null;

  if (currentError) return <p className="text-[12px]" style={{ color: "var(--neg)" }}>{currentError}</p>;
  if (!currentCatalog) {
    return (
      <div className="flex flex-col gap-4">
        <SkeletonCards label={`Loading captured GEX history for ${ticker}…`} count={1} lines={2} />
        <SkeletonGrid label="Loading captured GEX strike profiles…" rows={10} columns={4} />
      </div>
    );
  }
  if (!timeline.length) return <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>No captured GEX snapshots for {ticker}. Capture history first, then return here to replay it.</p>;

  return (
    <div className="flex flex-col gap-4" style={{ color: "var(--text)" }}>
      <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><h2 className="text-[15px] font-semibold">{ticker} captured GEX replay</h2><p className="mt-1 text-[11px]" style={{ color: "var(--text-dim)" }}>{timeline.length} captured profiles · {currentCatalog.counts.tickers} tracked tickers · read only</p></div>
          <div className="flex gap-2">
            <button type="button" disabled={index === 0} onClick={() => setSelectedId(timeline[index - 1]?.id ?? selectedId)} className="rounded px-3 py-1 text-[11px] disabled:opacity-40" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>Previous</button>
            <button type="button" onClick={() => setPlaying((value) => !value)} className="rounded px-3 py-1 text-[11px]" style={{ background: playing ? "var(--nav-active)" : "var(--panel-2)", border: "1px solid var(--accent)" }}>{playing ? "Pause" : "Play"}</button>
            <button type="button" disabled={index >= timeline.length - 1} onClick={() => setSelectedId(timeline[index + 1]?.id ?? selectedId)} className="rounded px-3 py-1 text-[11px] disabled:opacity-40" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>Next</button>
          </div>
        </div>
        <input aria-label="GEX replay timeline" className="mt-4 w-full accent-[var(--accent)]" type="range" min={0} max={timeline.length - 1} value={index} onChange={(event) => { setPlaying(false); setSelectedId(timeline[Number(event.target.value)].id); }} />
        <div className="mt-1 flex justify-between text-[9.5px]" style={{ color: "var(--text-mute)" }}><span>{stamp(timeline[0].captured_at)}</span><span>{currentPayload ? stamp(currentPayload.snapshot.captured_at) : "Loading selected snapshot…"}</span><span>{stamp(timeline.at(-1)!.captured_at)}</span></div>
        <p className="mt-3 text-[10.5px]" style={{ color: "var(--text-mute)" }}>{currentCatalog.caveat} Missing gamma or open interest remains unknown.</p>
      </section>

      {currentPayload ? <>
        <section className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {[["Spot", currentPayload.snapshot.spot], ["Call wall", currentPayload.snapshot.call_wall_strike], ["Put wall", currentPayload.snapshot.put_wall_strike], ["Gamma flip", currentPayload.snapshot.gamma_flip_level], ["Global max", currentPayload.snapshot.global_max_strike], ["Contracts", currentPayload.snapshot.contracts]].map(([label, value]) =>
            <div key={String(label)} className="rounded-[8px] p-3" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><div className="text-[9px] uppercase" style={{ color: "var(--text-mute)" }}>{label}</div><div className="mt-1 font-mono text-[13px]">{metric(value as number | null)}</div></div>
          )}
        </section>
        <section className="overflow-hidden rounded-[var(--radius)]" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
          {/* The five fixed columns are intentionally wider than a phone. Keep the
              horizontal scroll owner here so the panel remains usable at narrow widths
              instead of clipping Call/Put/Net GEX and Coverage off the viewport. */}
          <div className="overflow-x-auto">
            <div
              className="min-w-[620px]"
              role="table"
              aria-label={`${ticker} captured GEX strike profile`}
            >
              <div
                className="grid grid-cols-[90px_repeat(3,minmax(110px,1fr))_90px] gap-px px-3 py-2 text-[9px] font-semibold uppercase"
                role="row"
                style={{ background: "var(--panel-2)", color: "var(--text-mute)" }}
              >
                <span role="columnheader">Strike</span>
                <span role="columnheader">Call GEX</span>
                <span role="columnheader">Put GEX</span>
                <span role="columnheader">Net GEX</span>
                <span role="columnheader">Coverage</span>
              </div>
              <div className="max-h-[620px] overflow-y-auto" role="rowgroup">
                {currentPayload.strikes.map((row) => <div key={row.strike} className="grid grid-cols-[90px_repeat(3,minmax(110px,1fr))_90px] items-center gap-px border-t px-3 py-1 font-mono text-[10.5px]" role="row" aria-label={`Strike ${row.strike}`} style={{ borderColor: "var(--line)", background: nearest?.strike === row.strike ? "color-mix(in srgb, var(--gold) 9%, transparent)" : undefined }}>
                  <span role="cell" style={{ color: nearest?.strike === row.strike ? "var(--gold)" : "var(--text)" }}>{row.strike.toFixed(2)}</span>
                  {[row.call_gex, row.put_gex, row.net_gex].map((value, cell) => <span key={cell} role="cell" aria-label={`${["Call GEX", "Put GEX", "Net GEX"][cell]} ${value == null ? "unknown" : formatDollar(value)}`} className="rounded px-2 py-1 text-right" style={{ background: value == null ? "var(--panel-2)" : getCellColor(value, maxAbs), color: value == null ? "var(--text-mute)" : "white" }}>{value == null ? "unknown" : formatDollar(value)}</span>)}
                  <span role="cell" aria-label={`Coverage ${row.available_cells} of ${row.listed_cells}`} className="text-right" style={{ color: row.incomplete ? "var(--gold)" : "var(--text-dim)" }}>{row.available_cells}/{row.listed_cells}{row.incomplete ? "*" : ""}</span>
                </div>)}
              </div>
            </div>
          </div>
          <p className="border-t px-4 py-3 text-[10px]" style={{ borderColor: "var(--line)", color: "var(--text-mute)" }}>{currentPayload.aggregation} * Incomplete means one or more captured expiration cells lacked gamma or open interest.</p>
        </section>
      </> : (
        <SkeletonGrid label="Loading selected GEX strike profile…" rows={10} columns={4} />
      )}
    </div>
  );
}
