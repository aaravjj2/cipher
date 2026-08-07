"use client";

import { useEffect, useState, type ComponentType, type ReactNode, type SVGProps } from "react";
import { ClockIcon, CrownIcon, KeyIcon } from "@/components/icons";
import {
  fetchHealth,
  fetchResearchStatus,
  fetchWeightLabStatus,
  type RealHealth,
  type RealResearchStatus,
  type RealWeightLabStatus,
} from "@/lib/api";
import { readLocal, writeLocal } from "@/lib/localStorage";

/**
 * Settings panel — real connection status (from /api/health) and Cipher Model calibration
 * status (from /api/weight-lab), replacing the AccessObsidian-clone's "paste your own
 * Alpaca API key into the browser" onboarding flow. That flow described the SaaS product
 * being cloned, not cipher-system's actual architecture: Alpaca credentials live only in
 * the local core service's .env and the browser never sees them (confirmed against the
 * legacy vanilla-JS frontend's Settings copy — "Keys are server-side only"). There's also
 * no "plan"/subscription concept here — this is a local, single-user research tool.
 */

const REFRESH_INTERVAL_KEY = "cipher_refresh_interval_v1";

type IconComponent = ComponentType<SVGProps<SVGSVGElement>>;

// ---------------------------------------------------------------------------
// Shared building blocks
// ---------------------------------------------------------------------------

function Card({ children }: { children: ReactNode }) {
  return (
    <section
      className="flex flex-col gap-4 rounded-[var(--radius)] p-6"
      style={{ background: "var(--panel)", border: "1px solid var(--line)" }}
    >
      {children}
    </section>
  );
}

function CardHeading({ icon: Icon, title, right }: { icon: IconComponent; title: string; right?: ReactNode }) {
  return (
    <div className="flex flex-row items-center justify-between gap-3">
      <div className="flex flex-row items-center gap-2">
        <Icon width={18} height={18} style={{ color: "var(--accent)" }} />
        <h2 className="text-[15px] font-bold" style={{ color: "var(--text)" }}>
          {title}
        </h2>
      </div>
      {right}
    </div>
  );
}

function StatusBadge({ ok, okLabel, badLabel }: { ok: boolean; okLabel: string; badLabel: string }) {
  const color = ok ? "var(--success)" : "var(--neg)";
  return (
    <span
      className="text-[11px] font-bold px-3 py-[6px] rounded-full uppercase shrink-0"
      style={{
        background: `color-mix(in srgb, ${color} 18%, transparent)`,
        color,
        border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
        letterSpacing: "0.08em",
      }}
    >
      {ok ? okLabel : badLabel}
    </span>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}>
      {children}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-row items-center justify-between gap-3 text-[13px]" style={{ color: "var(--text-dim)" }}>
      <span>{label}</span>
      <strong style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>{value}</strong>
    </div>
  );
}

type IntervalOption = "10" | "15" | "30" | "60";
const INTERVAL_OPTIONS: { value: IntervalOption; label: string }[] = [
  { value: "10", label: "10s" },
  { value: "15", label: "15s" },
  { value: "30", label: "30s" },
  { value: "60", label: "60s" },
];

function IntervalPills({ value, onChange }: { value: IntervalOption; onChange: (v: IntervalOption) => void }) {
  return (
    <div className="flex flex-row items-center gap-[2px] rounded-[8px] p-[2px] w-fit" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      {INTERVAL_OPTIONS.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className="rounded-[6px] px-3 py-[6px] text-[12px] font-semibold transition-colors duration-150"
            style={{ background: active ? "var(--nav-active)" : "transparent", color: active ? "var(--text)" : "var(--text-dim)", fontFamily: "var(--font-mono)" }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

function YourPlanCard() {
  return (
    <Card>
      <CardHeading
        icon={CrownIcon}
        title="Your plan"
        right={
          <span
            className="text-[11px] font-bold px-3 py-[6px] rounded-full uppercase shrink-0"
            style={{
              background: "color-mix(in srgb, var(--gold) 22%, transparent)",
              color: "var(--gold)",
              border: "1px solid color-mix(in srgb, var(--gold) 35%, transparent)",
              letterSpacing: "0.08em",
            }}
          >
            Local
          </span>
        }
      />
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
        Every research feature is unlocked locally — this is a clean-room local
        implementation, not an Access Obsidian account entitlement, and there&apos;s no
        subscription tied to it.
      </p>
    </Card>
  );
}

function PreferencesCard() {
  const [refreshInterval, setRefreshInterval] = useState<IntervalOption>("15");

  useEffect(() => {
    setRefreshInterval(readLocal<IntervalOption>(REFRESH_INTERVAL_KEY, "15"));
  }, []);

  const handleChange = (v: IntervalOption) => {
    setRefreshInterval(v);
    writeLocal(REFRESH_INTERVAL_KEY, v);
  };

  return (
    <Card>
      <CardHeading icon={ClockIcon} title="Preferences" />
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
        Saved to this browser. Timestamps use your device&apos;s local timezone.
      </p>

      <div className="flex flex-col gap-2">
        <FieldLabel>Auto-refresh interval</FieldLabel>
        <IntervalPills value={refreshInterval} onChange={handleChange} />
      </div>
    </Card>
  );
}

function ConnectionCard() {
  const [health, setHealth] = useState<RealHealth | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then(setHealth)
      .catch(() => setError(true));
    return () => controller.abort();
  }, []);

  const connected = Boolean(health?.market_data_configured);

  return (
    <Card>
      <CardHeading
        icon={KeyIcon}
        title="Core connection"
        right={<StatusBadge ok={connected} okLabel="Connected" badLabel={error ? "Unreachable" : "Checking…"} />}
      />

      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
        Cipher reads Alpaca options and price data through the local core service to build
        the Strike Matrix, Night Vision charts, Trident, and Spyglass flow. Credentials live
        only in that service&apos;s local <code>.env</code> file — this browser never sees
        keys, and Cipher never places trades or generates orders.
      </p>

      {health && (
        <div className="flex flex-col gap-1.5 mt-1">
          <StatRow label="Options feed" value={health.default_options_feed.toUpperCase()} />
          <StatRow label="Stock feed" value={health.default_stock_feed.toUpperCase()} />
          <StatRow label="Mode" value={health.read_only ? "Read-only" : "Live"} />
        </div>
      )}
    </Card>
  );
}

/**
 * Research status — restores a disclosure the frontend migration dropped.
 *
 * The retired vanilla UI rendered a `researchStatus` view stating EXECUTION
 * AUTHORITY: NONE, and a test asserted it existed. When the Next.js rewrite
 * replaced app/public, the view went with it and the assertion started failing on
 * a missing file, which read like a stale test rather than a removed guarantee.
 * The API (/api/research-status) never went away, so the surface is rebuilt here.
 */
function ResearchStatusCard() {
  const [status, setStatus] = useState<RealResearchStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchResearchStatus(controller.signal)
      .then(setStatus)
      .catch(() => setError(true));
    return () => controller.abort();
  }, []);

  return (
    <Card>
      <CardHeading icon={KeyIcon} title="Research status" />
      <div data-view="researchStatus" className="flex flex-col gap-4">
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
        Cipher runs research loops locally — backtests, scans and signal studies. None of
        them can reach a broker.
      </p>

      <div className="flex flex-col gap-1.5 mt-1">
        <StatRow label="EXECUTION AUTHORITY" value="NONE" />
        <StatRow
          label="Live execution present"
          value={status ? (status.live_execution_present ? "YES" : "NO") : "—"}
        />
        <StatRow label="Mode" value={status?.read_only === false ? "Live" : "Read-only"} />
        <StatRow label="Operator status" value={status?.initialized ? "initialized" : "not initialized"} />
      </div>

      {error && (
        <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>
          Research status unavailable — the core service may not be running.
        </p>
      )}
      {status?.message && (
        <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>
          {status.message}
        </p>
      )}
      </div>
    </Card>
  );
}

function WeightLabCard() {
  const [status, setStatus] = useState<RealWeightLabStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchWeightLabStatus(controller.signal)
      .then(setStatus)
      .catch(() => setError(true));
    return () => controller.abort();
  }, []);

  return (
    <Card>
      <CardHeading icon={ClockIcon} title="Cipher weight lab" />
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
        Calibrates Setup Scanner scores against dated commercial-card labels. Public-OI GEX
        is a heuristic, not verified dealer positioning — rank/score approximation only.
      </p>

      {error && <p className="text-[13px]" style={{ color: "var(--neg)" }}>Weight lab status unavailable.</p>}

      {status && (
        <div className="flex flex-col gap-1.5">
          <StatRow label="Cipher Model rows" value={status.commercial_rows} />
          <StatRow label="Flash runway rows" value={status.flash_rows} />
          <StatRow label="Local feature tickers" value={status.feature_tickers} />
          <StatRow
            label="Cipher R² / τ / n"
            value={
              status.weights_summary
                ? `${status.weights_summary.r_squared} / ${status.weights_summary.kendall_tau_rank} / n=${status.weights_summary.n}`
                : "—"
            }
          />
          <StatRow
            label="Flash R² / τ / n"
            value={
              status.flash_weights_summary
                ? `${status.flash_weights_summary.r_squared} / ${status.flash_weights_summary.kendall_tau_rank} / n=${status.flash_weights_summary.n}`
                : "—"
            }
          />
          <StatRow label="Cipher active" value={status.active ? "Yes" : "No"} />
          <StatRow label="Flash active" value={status.flash_active ? "Yes" : "No"} />
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Settings() {
  return (
    <section className="settings flex flex-col gap-4" style={{ fontFamily: "var(--font-sans)", color: "var(--text)" }}>
      <div className="flex flex-col gap-1">
        <h1 className="text-[22px] font-bold" style={{ color: "var(--text)" }}>
          Settings
        </h1>
        <p className="text-[13px]" style={{ color: "var(--text-mute)" }}>
          Local read-only research terminal — connection status and calibration for the
          Cipher core service.
        </p>
      </div>

      <YourPlanCard />
      <PreferencesCard />
      <ConnectionCard />
      <ResearchStatusCard />
      <WeightLabCard />
    </section>
  );
}

export default Settings;
