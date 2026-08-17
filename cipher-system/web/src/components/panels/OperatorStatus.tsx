"use client";

import { useEffect, useState } from "react";
import { fetchOperatorStatus, type OperatorStatus as Payload } from "@/lib/api";

const size = (value = 0) => value >= 1e9 ? `${(value / 1e9).toFixed(1)} GB` : value >= 1e6 ? `${(value / 1e6).toFixed(1)} MB` : `${(value / 1e3).toFixed(1)} KB`;
const age = (seconds?: number) => seconds == null ? "—" : seconds < 120 ? `${Math.round(seconds)}s` : seconds < 7200 ? `${Math.round(seconds / 60)}m` : `${(seconds / 3600).toFixed(1)}h`;

export function OperatorStatus() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { const ctrl = new AbortController(); fetchOperatorStatus(ctrl.signal).then(setData).catch((reason) => { if (!ctrl.signal.aborted) setError(reason instanceof Error ? reason.message : "Operator status unavailable"); }); return () => ctrl.abort(); }, []);
  if (!data) return <p style={{ color: "var(--text-mute)" }}>{error || "Checking local terminal health…"}</p>;
  const card = "rounded-xl p-4 text-[11px]";
  return <div className="space-y-4" style={{ color: "var(--text)" }}>
    <section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="flex items-start justify-between gap-4"><div><h1 className="text-xl font-bold">Local operator status</h1><p style={{ color: "var(--text-mute)" }}>Storage, capture continuity, caches, database readability, and restore readiness. No execution capability.</p></div><b style={{ color: data.exceptions.length ? "var(--gold)" : "var(--positive)" }}>{data.exceptions.length ? `${data.exceptions.length} exception(s)` : "Healthy checks"}</b></div>
      <p className="mt-3">Disk free: <b>{size(data.disk.free_bytes)} ({data.disk.free_percent}%)</b> · runway {data.disk.runway_days != null ? <b>{data.disk.runway_days.toFixed(1)} days</b> : data.disk.runway_status.toLowerCase().replaceAll("_", " ")}</p><p style={{ color: "var(--text-mute)" }}>{data.disk.detail}</p>
    </section>
    {data.exceptions.length > 0 && <section className={card} style={{ background: "var(--panel)", border: "1px solid var(--gold)" }}><h2 className="font-semibold">Exceptions</h2>{data.exceptions.map((item) => <p key={item} style={{ color: "var(--gold)" }}>{item}</p>)}</section>}
    <div className="grid gap-3 xl:grid-cols-2"><section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><h2 className="mb-2 text-sm font-semibold">Active databases</h2>{data.databases.map((row) => <div key={row.path} className="flex justify-between border-b py-2" style={{ borderColor: "var(--line)" }}><span>{row.path}<br/><small style={{ color: "var(--text-mute)" }}>{size(row.bytes)} · updated {age(row.age_seconds)}</small></span><b style={{ color: row.status === "ERROR" ? "var(--negative)" : "var(--positive)" }}>{row.status}<br/><small>{row.integrity}</small></b></div>)}</section>
    <section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><h2 className="mb-2 text-sm font-semibold">Capture continuity</h2>{Object.entries(data.captures).map(([name, row]) => <div key={name} className="flex justify-between border-b py-2" style={{ borderColor: "var(--line)" }}><span>{name.replaceAll("_", " ")}<br/><small style={{ color: "var(--text-mute)" }}>{row.path ?? row.detail}</small></span><b>{row.status}<br/><small>{age(row.age_seconds)}</small></b></div>)}</section></div>
    <div className="grid gap-3 xl:grid-cols-2"><section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><h2 className="mb-2 text-sm font-semibold">Core caches</h2>{data.caches.map((row) => <p key={row.name}>{row.name}: <b>{row.entries}</b> entries · {row.hit_rate_pct != null ? `${row.hit_rate_pct}% hit rate · ` : ""}TTL {row.ttl_seconds}s · average age {age(row.avg_age_s ?? undefined)}</p>)}</section><section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}><h2 className="text-sm font-semibold">Restore readiness</h2><p>Status: <b style={{ color: data.backup.status === "VERIFIED" ? "var(--positive)" : "var(--gold)" }}>{data.backup.status}</b></p><p>{data.backup.created_at ? `${data.backup.store_count} state stores · ${data.backup.created_at}` : data.backup.detail}</p><p className="mt-2" style={{ color: "var(--text-mute)" }}>Create a restore-verified copy with <code>python3 scripts/backup_local_state.py</code>. Large reproducible market archives are deliberately outside this small-state backup.</p></section></div>
    <div className="grid gap-3 xl:grid-cols-2">
      <section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <h2 className="mb-2 text-sm font-semibold">Provider telemetry · 7 days</h2>
        {data.provider_telemetry.providers.length === 0 && <p style={{ color: "var(--text-mute)" }}>No provider requests recorded yet.</p>}
        {data.provider_telemetry.providers.slice(0, 12).map((row) => <div key={`${row.provider}-${row.operation}`} className="flex justify-between gap-3 border-b py-2" style={{ borderColor: "var(--line)" }}><span>{row.provider} · {row.operation}<br/><small style={{ color: "var(--text-mute)" }}>{row.requests} request(s) · avg {row.avg_latency_ms} ms</small></span><b className="text-right" style={{ color: row.error_count ? "var(--gold)" : "var(--positive)" }}>p95 {row.p95_latency_ms} ms<br/><small>{row.error_rate_pct}% errors</small></b></div>)}
      </section>
      <section className={card} style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <h2 className="mb-2 text-sm font-semibold">Retention & off-host archive</h2>
        <p>Retention: <b>{data.retention.mode}</b> · {data.retention.candidate_count} candidate file(s) · {size(data.retention.candidate_bytes)}</p>
        <p style={{ color: "var(--text-mute)" }}>The policy is visibility-only; destructive action is disabled.</p>
        <p className="mt-3">Archive: <b style={{ color: data.off_host_archive.status === "VERIFIED_RECEIPTS" ? "var(--positive)" : "var(--gold)" }}>{data.off_host_archive.status.replaceAll("_", " ")}</b></p>
        <p>{data.off_host_archive.receipts != null ? `${data.off_host_archive.receipts} checksum-verified receipt(s) · ${data.off_host_archive.verified_and_pruned ?? 0} locally pruned` : data.off_host_archive.detail}</p>
      </section>
    </div>
    <p className="text-[10px]" style={{ color: "var(--text-mute)" }}>Generated {data.generated_at}. Large databases receive a read-only schema probe; expensive whole-file integrity scans are labelled NOT_RUN_LARGE_FILE.</p>
  </div>;
}
