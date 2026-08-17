"use client";

import { useEffect, useMemo, useState } from "react";
import {
  fetchResearchRanking,
  type ResearchRankedGroup,
  type ResearchRanking,
} from "@/lib/api";
import { SkeletonCards } from "@/components/ui/skeleton";

/**
 * Beliefs panel — what Cipher currently believes, how strongly, and what would change its
 * mind. Sourced from /api/research-ranking (core/autopilot.py), which serves the last
 * autopilot pass over every research report under runtime/data.
 *
 * The other twenty panels each answer a question about a ticker. This one answers a question
 * about Cipher: of everything it has researched, what survives its own standards. Before the
 * shared result envelope existed, that question was unanswerable — eleven of twelve strategy
 * labs emitted results in private shapes and nothing could rank across them.
 *
 * Three things this panel refuses to do, each because the alternative has already misled
 * someone here:
 *
 *   - It never presents a stale pass as current. The data is a cached artifact, so `stale`
 *     and the age are rendered, not hidden.
 *   - It never labels the metric column as the result. A walkforward's verdict is governed
 *     by its harshest execution model, so a rejected study can still show a profit factor
 *     above 1; the column says "best case" for exactly that reason.
 *   - It never colours a tier green. Purple is up here and red is down, as everywhere else
 *     in Cipher.
 */

const TIER_META: Record<
  string,
  { label: string; blurb: string; tone: "up" | "warn" | "mute" | "down" }
> = {
  "1": { label: "Believe it", blurb: "selectable, cost measured", tone: "up" },
  "2": { label: "Believe it, within the assumption", blurb: "selectable, cost assumed", tone: "up" },
  "3": { label: "A further experiment would pay", blurb: "inconclusive", tone: "warn" },
  "4": { label: "Looked, does not work", blurb: "rejected", tone: "mute" },
  "5": { label: "Cannot be evaluated yet", blurb: "blocked", tone: "down" },
};

function toneColor(tone: "up" | "warn" | "mute" | "down"): string {
  if (tone === "up") return "var(--accent)";
  if (tone === "warn") return "var(--gold)";
  if (tone === "down") return "var(--neg)";
  return "var(--text-mute)";
}

function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "age unknown";
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-[10px] p-3"
      style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
    >
      {children}
    </div>
  );
}

function SectionTitle({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <div className="flex items-baseline gap-2 mb-2">
      <h3
        className="text-[11px] font-semibold tracking-[0.09em] uppercase"
        style={{ color: "var(--text-mute)" }}
      >
        {children}
      </h3>
      {note ? (
        <span className="text-[10px]" style={{ color: "var(--text-mute)" }}>
          {note}
        </span>
      ) : null}
    </div>
  );
}

function TierLadder({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  return (
    <div className="flex flex-col gap-1">
      {Object.keys(TIER_META).map((tier) => {
        const meta = TIER_META[tier];
        const count = counts[tier] ?? 0;
        const share = total > 0 ? (count / total) * 100 : 0;
        return (
          <div key={tier} className="flex items-center gap-2">
            <span
              className="text-[10px] font-mono w-10 shrink-0 tabular-nums"
              style={{ color: "var(--text-mute)" }}
            >
              tier {tier}
            </span>
            <span
              className="text-[12px] font-mono w-8 shrink-0 text-right tabular-nums"
              style={{ color: count > 0 ? toneColor(meta.tone) : "var(--text-mute)" }}
            >
              {count}
            </span>
            {/* Label before the bar. With the bar growing first it pushed every label to the
                far right edge, which made the bar read as a separator between two columns
                rather than as the quantity it is. */}
            <span className="text-[11px] shrink-0 w-[19rem] truncate" style={{ color: "var(--text)" }}>
              {meta.label}
              <span className="ml-1" style={{ color: "var(--text-mute)" }}>
                ({meta.blurb})
              </span>
            </span>
            <div
              className="h-[6px] rounded-full grow min-w-0 max-w-[22rem]"
              style={{ background: "var(--panel)" }}
              role="presentation"
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${share}%`,
                  background: count > 0 ? toneColor(meta.tone) : "transparent",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Group({ group }: { group: ResearchRankedGroup }) {
  return (
    <div className="mb-4 last:mb-0">
      <SectionTitle note="not comparable to other groups">
        {group.metric} ({group.unit})
      </SectionTitle>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "var(--text-mute)" }}>
              <th className="text-left font-medium py-1 pr-3 whitespace-nowrap">tier</th>
              <th className="text-right font-medium py-1 pr-3 whitespace-nowrap">best case</th>
              <th className="text-right font-medium py-1 pr-3 whitespace-nowrap">sample</th>
              <th className="text-left font-medium py-1 pr-3">study</th>
              <th className="text-left font-medium py-1">cost basis</th>
            </tr>
          </thead>
          <tbody>
            {group.results.slice(0, 12).map((row) => {
              const meta = TIER_META[String(row.evidence_tier)];
              return (
                <tr key={row.study_id} style={{ borderTop: "1px solid var(--line)" }}>
                  <td className="py-1 pr-3 font-mono tabular-nums" style={{ color: toneColor(meta?.tone ?? "mute") }}>
                    {row.evidence_tier}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums" style={{ color: "var(--text)" }}>
                    {row.best_value === null ? "n/a" : row.best_value.toFixed(3)}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums" style={{ color: "var(--text-mute)" }}>
                    {row.observations}
                  </td>
                  <td className="py-1 pr-3" style={{ color: "var(--text)" }}>
                    {row.study_id}
                  </td>
                  <td className="py-1 font-mono text-[10px]" style={{ color: "var(--text-mute)" }}>
                    {row.cost_basis}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {group.results.length > 12 ? (
        <p className="text-[10px] mt-1" style={{ color: "var(--text-mute)" }}>
          {group.results.length - 12} more not shown
        </p>
      ) : null}
    </div>
  );
}

export function Beliefs() {
  const [data, setData] = useState<ResearchRanking | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchResearchRanking(controller.signal)
      .then((payload) => setData(payload))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "could not load the research ranking");
      });
    return () => controller.abort();
  }, []);

  const tierCounts = useMemo(() => data?.tier_counts ?? {}, [data]);

  if (error) {
    return (
      <div className="p-3">
        <Card>
          <p className="text-[12px]" style={{ color: "var(--neg)" }}>
            {error}
          </p>
        </Card>
      </div>
    );
  }

  if (!data) return <SkeletonCards label="Reading the last research pass…" count={3} lines={3} />;

  if (!data.available) {
    return (
      <div className="p-3">
        <Card>
          <SectionTitle>No pass recorded</SectionTitle>
          <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>
            {data.reason ?? "The autopilot has not produced a report yet."}
          </p>
        </Card>
      </div>
    );
  }

  const selectableCount = data.selectable?.length ?? 0;

  return (
    <div className="p-3 flex flex-col gap-3 overflow-y-auto">
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-[13px] font-medium" style={{ color: "var(--text)" }}>
            {data.headline ?? "Cipher autopilot"}
          </p>
          <span
            className="text-[10px] font-mono px-[6px] py-[2px] rounded-[5px] whitespace-nowrap"
            style={{
              // Staleness is a fact about the data, so it is rendered rather than smoothed
              // over. A cached belief shown as current is the failure this panel exists to
              // avoid.
              background: data.stale ? "color-mix(in srgb, var(--neg) 18%, transparent)" : "var(--panel)",
              color: data.stale ? "var(--neg)" : "var(--text-mute)",
              border: "1px solid var(--line)",
            }}
          >
            {data.stale ? "STALE · " : ""}
            {formatAge(data.age_seconds)}
          </span>
        </div>
        <p className="text-[10px] mt-1 font-mono" style={{ color: "var(--text-mute)" }}>
          {data.coverage
            ? `${data.coverage.adapted} studies ranked · ${data.coverage.unadapted} without an adapter`
            : ""}
          {data.commit ? ` · ${data.commit.slice(0, 12)}` : ""}
        </p>
        {data.coverage ? (
          <p className="text-[10px] mt-1" style={{ color: "var(--text-mute)" }}>
            {data.coverage.note}
          </p>
        ) : null}
      </Card>

      {data.capture_health?.available ? (
        <Card>
          <SectionTitle note={data.capture_health.profile_stale ? "profile is stale" : undefined}>
            Measured execution coverage
          </SectionTitle>
          <p className="text-[12px]" style={{ color: "var(--text)" }}>
            <span className="font-mono" style={{ color: "var(--accent)" }}>
              {data.capture_health.distinct_days ?? "?"}
            </span>{" "}
            capture days,{" "}
            <span
              className="font-mono"
              style={{ color: data.capture_health.gap_count > 0 ? "var(--neg)" : "var(--accent)" }}
            >
              {data.capture_health.gap_count}
            </span>{" "}
            imperfect — {data.capture_health.verdict}
          </p>
          {/* Named individually because a day not captured cannot be captured later, so each
              one permanently narrows the window a future study can be costed against. */}
          {data.capture_health.missing_weekdays.length > 0 ? (
            <p className="text-[10px] mt-1 font-mono" style={{ color: "var(--neg)" }}>
              no capture: {data.capture_health.missing_weekdays.join(", ")}
            </p>
          ) : null}
          {data.capture_health.sparse_days.length > 0 ? (
            <p className="text-[10px] mt-[2px] font-mono" style={{ color: "var(--text-mute)" }}>
              partial: {data.capture_health.sparse_days.join(", ")}
            </p>
          ) : null}
        </Card>
      ) : null}

      <Card>
        <SectionTitle note={`${selectableCount} selectable`}>How strongly, by tier</SectionTitle>
        <TierLadder counts={tierCounts} />
      </Card>

      <Card>
        <SectionTitle>What would change its mind</SectionTitle>
        {(data.recommended_actions?.length ?? 0) > 0 ? (
          <ol className="flex flex-col gap-3 list-none m-0 p-0">
            {data.recommended_actions!.map((action, index) => (
              <li key={action.clears_blocker}>
                <p className="text-[12px]" style={{ color: "var(--text)" }}>
                  <span className="font-mono mr-1" style={{ color: "var(--accent)" }}>
                    {index + 1}.
                  </span>
                  {action.action}
                  <span className="ml-1 font-mono text-[10px]" style={{ color: "var(--accent)" }}>
                    unblocks {action.unblocks_studies}
                  </span>
                </p>
                {action.detail ? (
                  <p className="text-[11px] mt-[2px]" style={{ color: "var(--text-mute)" }}>
                    {action.detail}
                  </p>
                ) : null}
                <p className="text-[10px] mt-[2px] font-mono" style={{ color: "var(--text-mute)" }}>
                  latency: {action.latency}
                </p>
                {action.limitation ? (
                  <p className="text-[10px] mt-[2px]" style={{ color: "var(--neg)" }}>
                    {action.limitation}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>
            {data.nothing_to_run_because || "Nothing is worth running."}
          </p>
        )}
      </Card>

      {(data.changes?.length ?? 0) > 0 ? (
        <Card>
          <SectionTitle>Changed since the previous pass</SectionTitle>
          <ul className="flex flex-col gap-1 list-none m-0 p-0">
            {data.changes!.slice(0, 10).map((change, index) => (
              <li key={`${change.kind}-${change.study_id ?? index}`} className="text-[11px]">
                <span className="font-mono mr-1" style={{ color: "var(--accent)" }}>
                  {change.kind}
                </span>
                <span style={{ color: "var(--text)" }}>{change.study_id ?? ""}</span>
                <span className="ml-1" style={{ color: "var(--text-mute)" }}>
                  {change.detail}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {(data.groups?.length ?? 0) > 0 ? (
        <Card>
          {data.groups!.map((group) => (
            <Group key={group.metric} group={group} />
          ))}
        </Card>
      ) : null}

      <p className="text-[10px] px-1" style={{ color: "var(--text-mute)" }}>
        Research only. Cipher holds no live-order authority
        {data.highest_possible_output ? `; its highest output is ${data.highest_possible_output}` : ""}.
      </p>
    </div>
  );
}
