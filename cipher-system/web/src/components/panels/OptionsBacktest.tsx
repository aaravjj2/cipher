"use client";

import { useMemo, useState, useEffect } from "react";
import {
  fetchOptionsBacktestCatalog,
  fetchOptionsBacktestReport,
  type OptionsBacktestCatalog,
  type OptionsBacktestDataset,
  type OptionsBacktestReportPayload,
} from "@/lib/api";

type LoadedReport = {
  id: string;
  data: OptionsBacktestReportPayload | null;
  error: string | null;
};

function labelOf(value: string): string {
  return value.replace(/_/g, " ");
}

function displayValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function bytes(value: number | null): string {
  if (value == null) return "—";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${value.toLocaleString("en-US")} B`;
}

function DatasetButton({
  dataset,
  active,
  onClick,
}: {
  dataset: OptionsBacktestDataset;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="w-full rounded-[9px] px-3 py-3 text-left transition-colors"
      style={{
        background: active ? "var(--nav-active)" : "var(--panel-2)",
        border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
      }}
    >
      <span className="block break-all text-[11.5px] font-semibold" style={{ color: "var(--text)" }}>
        {dataset.relative_path}
      </span>
      <span className="mt-1 block text-[10px] uppercase" style={{ color: "var(--text-mute)" }}>
        {dataset.status || "status unavailable"} · {bytes(dataset.database_size_bytes)}
      </span>
    </button>
  );
}

function CapabilityGrid({ dataset }: { dataset: OptionsBacktestDataset }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {Object.entries(dataset.capabilities).map(([name, value]) => {
        const supported = value === true;
        return (
          <div
            key={name}
            className="flex items-center justify-between gap-3 rounded-[8px] px-3 py-2 text-[10.5px]"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
          >
            <span className="capitalize" style={{ color: "var(--text-dim)" }}>{labelOf(name)}</span>
            <span
              className="font-semibold uppercase"
              style={{ color: supported ? "var(--accent)" : "var(--neg)" }}
            >
              {displayValue(value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function OptionsBacktest() {
  const [catalog, setCatalog] = useState<OptionsBacktestCatalog | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedReport, setLoadedReport] = useState<LoadedReport | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchOptionsBacktestCatalog(controller.signal)
      .then(setCatalog)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load the options catalog.");
        }
      });
    return () => controller.abort();
  }, []);

  const dataset = useMemo(() => {
    if (!catalog?.datasets.length) return null;
    return catalog.datasets.find((row) => row.id === selected) ?? catalog.datasets[0];
  }, [catalog, selected]);

  const reports = useMemo(
    () => catalog?.reports.filter((row) => row.dataset_id === dataset?.id) ?? [],
    [catalog, dataset]
  );

  async function openReport(reportId: string) {
    setLoadedReport({ id: reportId, data: null, error: null });
    try {
      const data = await fetchOptionsBacktestReport(reportId);
      setLoadedReport({ id: reportId, data, error: null });
    } catch (err) {
      setLoadedReport({
        id: reportId,
        data: null,
        error: err instanceof Error ? err.message : "Could not load this stored report.",
      });
    }
  }

  if (error) {
    return <p className="text-[12px]" style={{ color: "var(--neg)" }}>{error}</p>;
  }
  if (!catalog || !dataset) {
    return <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>Loading options research catalog…</p>;
  }

  return (
    <div className="flex flex-col gap-4" style={{ color: "var(--text)" }}>
      <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold">Historical options research</h2>
            <p className="mt-1 text-[11.5px]" style={{ color: "var(--text-dim)" }}>
              Stored datasets and completed lab reports. This page never launches a backtest job.
            </p>
          </div>
          <div className="flex gap-3 text-[10.5px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
            <span><b style={{ color: "var(--text)" }}>{catalog.counts.datasets}</b> datasets</span>
            <span><b style={{ color: "var(--text)" }}>{catalog.counts.reports}</b> reports</span>
            <span>read only</span>
          </div>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed" style={{ color: "var(--text-mute)" }}>
          {catalog.caveat}
        </p>
      </section>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="flex max-h-[680px] flex-col gap-2 overflow-y-auto rounded-[var(--radius)] p-3" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
          {catalog.datasets.map((row) => (
            <DatasetButton
              key={row.id}
              dataset={row}
              active={row.id === dataset.id}
              onClick={() => setSelected(row.id)}
            />
          ))}
        </aside>

        <div className="flex min-w-0 flex-col gap-4">
          <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="break-all text-[13px] font-semibold">{dataset.relative_path}</h3>
                <p className="mt-1 text-[10.5px] uppercase" style={{ color: "var(--text-mute)" }}>
                  {dataset.manifest_type} · {dataset.status || "unknown status"}
                </p>
              </div>
              <span className="rounded-[6px] px-2 py-1 text-[9.5px] font-bold uppercase" style={{ background: "color-mix(in srgb, var(--accent) 20%, transparent)", color: "var(--accent)" }}>
                bar/trade approximation
              </span>
            </div>

            <h4 className="mb-2 mt-5 text-[10px] font-semibold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Coverage</h4>
            <dl className="grid gap-x-5 gap-y-2 sm:grid-cols-2 xl:grid-cols-3">
              {Object.entries(dataset.coverage).filter(([, value]) => value != null).map(([name, value]) => (
                <div key={name} className="flex justify-between gap-3 border-b py-1 text-[11px]" style={{ borderColor: "var(--line)" }}>
                  <dt className="capitalize" style={{ color: "var(--text-dim)" }}>{labelOf(name)}</dt>
                  <dd className="text-right font-mono">{displayValue(value)}</dd>
                </div>
              ))}
              <div className="flex justify-between gap-3 border-b py-1 text-[11px]" style={{ borderColor: "var(--line)" }}>
                <dt style={{ color: "var(--text-dim)" }}>database</dt>
                <dd className="font-mono">{dataset.database_present ? bytes(dataset.database_size_bytes) : "missing"}</dd>
              </div>
            </dl>

            <h4 className="mb-2 mt-5 text-[10px] font-semibold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Capabilities</h4>
            <CapabilityGrid dataset={dataset} />

            <h4 className="mb-2 mt-5 text-[10px] font-semibold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>Manifest caveats — verbatim</h4>
            <ul className="flex flex-col gap-2">
              {dataset.caveats.map((caveat, index) => (
                <li key={`${caveat}-${index}`} className="rounded-[8px] px-3 py-2 text-[11px] leading-relaxed" style={{ background: "color-mix(in srgb, var(--neg) 9%, var(--panel-2))", color: "var(--text-dim)", border: "1px solid var(--line)" }}>
                  {caveat}
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-[12px] font-semibold">Stored reports</h3>
              <span className="text-[10px]" style={{ color: "var(--text-mute)" }}>{reports.length} linked</span>
            </div>
            {reports.length === 0 ? (
              <p className="mt-3 text-[11px]" style={{ color: "var(--text-mute)" }}>No completed report is linked to this dataset.</p>
            ) : (
              <div className="mt-3 flex flex-col gap-2">
                {reports.map((report) => {
                  const current = loadedReport?.id === report.id ? loadedReport : null;
                  return (
                    <div key={report.id} className="rounded-[9px] p-3" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="min-w-0">
                          <p className="break-all text-[10.5px] font-mono">{report.relative_path}</p>
                          <p className="mt-1 text-[9.5px]" style={{ color: "var(--text-mute)" }}>{bytes(report.size_bytes)} · {new Date(report.modified_at).toLocaleString()}</p>
                        </div>
                        <button type="button" onClick={() => openReport(report.id)} className="rounded-[6px] px-3 py-1.5 text-[10px] font-semibold" style={{ background: "var(--accent)", color: "white" }}>
                          {current && !current.data && !current.error ? "Loading…" : "View result"}
                        </button>
                      </div>
                      {current?.error && <p className="mt-2 text-[10.5px]" style={{ color: "var(--neg)" }}>{current.error}</p>}
                      {current?.data && (
                        <details className="mt-3" open>
                          <summary className="cursor-pointer text-[10px] font-semibold uppercase" style={{ color: "var(--accent)" }}>Bar/trade approximation result</summary>
                          <pre className="mt-2 max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-[7px] p-3 text-[10px] leading-relaxed" style={{ background: "var(--bg)", color: "var(--text-dim)" }}>
                            {JSON.stringify(current.data.result, null, 2)}
                          </pre>
                        </details>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export default OptionsBacktest;
