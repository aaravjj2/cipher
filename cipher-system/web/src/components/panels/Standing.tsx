"use client";

import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";
import { readLocal, writeLocal } from "@/lib/localStorage";
import { fetchStanding, type StandingStatus } from "@/lib/api";

/**
 * Standing panel — what is currently open (prospective registrations, shadow
 * positions) and how far each accrual clock has run, sourced from /api/standing
 * (core/app.py, backed by data/governance/research_registry.sqlite, the paper
 * executor's position table, and core/evidence_status.py). Cipher places no live
 * orders and has no realized P&L, so unlike the Journal panel this replaced —
 * which invented a Month P&L figure with no backend behind it — there is no P&L
 * headline here. Manual per-day notes are the one piece with no server backend;
 * they remain their own localStorage layer, keyed by date.
 */

const NOTES_STORAGE_KEY = "cipher_standing_notes_v1";

type DayNote = { date: string; note: string };

const DAY_LABELS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function firstWeekdayOfMonth(year: number, month: number): number {
  return new Date(year, month, 1).getDay();
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Small building blocks
// ---------------------------------------------------------------------------

function NavIconButton({ onClick, ariaLabel, children }: { onClick: () => void; ariaLabel: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      className="grid place-items-center w-7 h-7 rounded-[7px] shrink-0 transition-colors duration-150"
      style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-mute)" }}
    >
      {children}
    </button>
  );
}

function Section({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-[var(--radius)] p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="flex flex-row items-center justify-between gap-3">
        <h2 className="text-[13px] font-bold uppercase" style={{ letterSpacing: "0.06em", color: "var(--text)" }}>
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[12.5px] italic" style={{ color: "var(--text-mute)" }}>
      {children}
    </p>
  );
}

function StatusPill({ status }: { status: string }) {
  const settled = status === "AWAITING_LOCKED_ANALYSIS";
  const color = settled ? "var(--gold)" : "var(--accent)";
  return (
    <span
      className="rounded-[4px] px-[6px] py-[2px] text-[10px] font-bold uppercase whitespace-nowrap"
      style={{
        fontFamily: "var(--font-mono)",
        letterSpacing: "0.04em",
        background: `color-mix(in srgb, ${color} 20%, transparent)`,
        color,
      }}
    >
      {statusLabel(status)}
    </span>
  );
}

function RegistrationRow({ row }: { row: StandingStatus["prospective_registrations"][number] }) {
  const pct = Math.max(0, Math.min(100, row.progress_pct ?? 0));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-row items-baseline justify-between gap-3">
        <span className="text-[13px] font-semibold" style={{ color: "var(--text)" }}>{row.name}</span>
        <StatusPill status={row.status} />
      </div>
      <div className="h-[6px] w-full overflow-hidden rounded-full" style={{ background: "var(--panel-2)" }}>
        <div className="h-full rounded-full transition-[width] duration-300" style={{ width: `${pct}%`, background: "var(--accent)" }} />
      </div>
      <span className="text-[11px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>
        {row.scored_count} / {row.minimum_sample} scored · registered {row.created_at.slice(0, 10)}
      </span>
    </div>
  );
}

function ShadowPositionRow({ row }: { row: StandingStatus["shadow_positions"][number] }) {
  return (
    <div className="flex flex-row items-center justify-between gap-3 rounded-[8px] px-3 py-2" style={{ background: "var(--panel-2)" }}>
      <div className="flex flex-col gap-0.5">
        <span className="text-[13px] font-semibold" style={{ color: "var(--text)" }}>
          {row.ticker} <span style={{ color: "var(--text-mute)", fontWeight: 400 }}>{row.direction}</span>
        </span>
        <span className="text-[11px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>
          {row.symbol} · qty {row.quantity} @ {row.entry_price}
        </span>
      </div>
      <span className="text-[11px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}>
        opened {row.opened_at.slice(0, 10)}
      </span>
    </div>
  );
}

function AccrualClockRow({ clock }: { clock: StandingStatus["clocks"][number] }) {
  const pct = Math.max(0, Math.min(100, clock.progress_pct ?? 0));
  const done = clock.need != null && clock.have >= clock.need;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-row items-baseline justify-between gap-3">
        <span className="text-[13px] font-semibold" style={{ color: "var(--text)" }}>{clock.name}</span>
        <span className="text-[12px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
          {clock.have}{clock.need != null ? ` / ${clock.need}` : ""} {clock.unit}
        </span>
      </div>
      <div className="h-[6px] w-full overflow-hidden rounded-full" style={{ background: "var(--panel-2)" }}>
        <div
          className="h-full rounded-full transition-[width] duration-300"
          style={{ width: `${pct}%`, background: done ? "var(--success)" : "var(--accent)" }}
        />
      </div>
      <span className="text-[11px]" style={{ color: "var(--text-mute)" }}>unlocks {clock.unlocks}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Standing() {
  // `today` starts null and is set inside an effect (client-only) so the build-time
  // static-export render and the client hydration never disagree on "today's" date.
  const [today, setToday] = useState<{ year: number; month: number; day: number } | null>(null);
  const [view, setView] = useState<{ year: number; month: number } | null>(null);
  const [notes, setNotes] = useState<DayNote[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [formDate, setFormDate] = useState("");
  const [formNote, setFormNote] = useState("");
  const [status, setStatus] = useState<StandingStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const now = new Date();
    setToday({ year: now.getFullYear(), month: now.getMonth(), day: now.getDate() });
    setView({ year: now.getFullYear(), month: now.getMonth() });
    setFormDate(now.toISOString().slice(0, 10));
    setNotes(readLocal<DayNote[]>(NOTES_STORAGE_KEY, []));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchStanding(controller.signal).then(setStatus).catch(() => setError(true));
    return () => controller.abort();
  }, []);

  function shiftMonth(delta: number) {
    setView((v) => {
      if (!v) return v;
      const total = v.year * 12 + v.month + delta;
      return { year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 };
    });
  }

  function goToToday() {
    if (today) setView({ year: today.year, month: today.month });
  }

  function saveNote(e: React.FormEvent) {
    e.preventDefault();
    const next = [...notes.filter((n) => n.date !== formDate), { date: formDate, note: formNote }].filter((n) => n.note.trim());
    setNotes(next);
    writeLocal(NOTES_STORAGE_KEY, next);
    setFormNote("");
    setFormOpen(false);
  }

  function removeNote(date: string) {
    const next = notes.filter((n) => n.date !== date);
    setNotes(next);
    writeLocal(NOTES_STORAGE_KEY, next);
  }

  const isTodayVisible = Boolean(today && view && view.year === today.year && view.month === today.month);

  const noteByDay = useMemo(() => {
    const map = new Map<number, DayNote>();
    if (!view) return map;
    for (const entry of notes) {
      const d = new Date(`${entry.date}T00:00:00`);
      if (d.getFullYear() === view.year && d.getMonth() === view.month) map.set(d.getDate(), entry);
    }
    return map;
  }, [notes, view]);

  if (!view || !today) {
    return (
      <section className="flex items-center justify-center py-20" style={{ color: "var(--text-mute)" }}>
        Loading standing…
      </section>
    );
  }

  const leadingBlanks = firstWeekdayOfMonth(view.year, view.month);
  const totalDays = daysInMonth(view.year, view.month);
  const dayNumbers = Array.from({ length: totalDays }, (_, i) => i + 1);
  const registrations = status?.prospective_registrations ?? [];
  const shadowPositions = status?.shadow_positions ?? [];
  const clocks = status?.clocks ?? [];

  return (
    <section className="flex flex-col gap-5" style={{ fontFamily: "var(--font-sans)", color: "var(--text)" }}>
      {/* Title row */}
      <div className="flex flex-row items-center gap-3">
        <span
          className="block w-10 h-10 rounded-full shrink-0 bg-no-repeat bg-cover bg-center"
          style={{
            backgroundImage: "url(/seo/cipher-logo.jpg)",
            boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08), 0 4px 14px color-mix(in srgb, var(--accent) 30%, transparent)",
          }}
          aria-hidden="true"
        />
        <h1 className="text-[22px] sm:text-[24px] font-bold leading-tight" style={{ color: "var(--text)" }}>
          Standing
        </h1>
      </div>

      {error && (
        <p className="text-[12px]" style={{ color: "var(--text-mute)" }}>
          Standing unavailable — the core service may not be running.
        </p>
      )}

      {/* Open commitments */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Section title="Open prospective registrations">
          {registrations.length === 0 ? (
            <EmptyRow>No open prospective registrations.</EmptyRow>
          ) : (
            <div className="flex flex-col gap-3">
              {registrations.map((row) => <RegistrationRow key={row.prospective_test_id} row={row} />)}
            </div>
          )}
        </Section>

        <Section title="Open shadow positions">
          {shadowPositions.length === 0 ? (
            <EmptyRow>No open shadow positions.</EmptyRow>
          ) : (
            <div className="flex flex-col gap-2">
              {shadowPositions.map((row) => <ShadowPositionRow key={row.id} row={row} />)}
            </div>
          )}
        </Section>
      </div>

      <Section title="Accrual clocks">
        {clocks.length === 0 ? (
          <EmptyRow>No accrual clocks reported.</EmptyRow>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {clocks.map((clock) => <AccrualClockRow key={clock.name} clock={clock} />)}
          </div>
        )}
      </Section>

      {/* Notes calendar */}
      <div className="flex flex-row flex-wrap items-center justify-between gap-3">
        <div className="flex flex-row items-center gap-3">
          <NavIconButton ariaLabel="Previous month" onClick={() => shiftMonth(-1)}>
            <ChevronLeftIcon width={15} height={15} />
          </NavIconButton>
          <span className="text-[16px] sm:text-[18px] font-bold min-w-[150px] text-center" style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
            {MONTH_NAMES[view.month]} {view.year}
          </span>
          <NavIconButton ariaLabel="Next month" onClick={() => shiftMonth(1)}>
            <ChevronRightIcon width={15} height={15} />
          </NavIconButton>
          <button
            type="button"
            onClick={goToToday}
            disabled={isTodayVisible}
            className="rounded-[9px] px-4 py-[7px] text-[12.5px] font-bold transition-opacity duration-150"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--ring))", color: "#ffffff", opacity: isTodayVisible ? 0.55 : 1 }}
          >
            Today
          </button>
        </div>

        <button
          type="button"
          onClick={() => setFormOpen((v) => !v)}
          className="flex flex-row items-center gap-[7px] rounded-[9px] px-[14px] py-[9px] text-[12.5px] font-bold"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
        >
          <span style={{ fontFamily: "var(--font-mono)" }}>{formOpen ? "Close" : "+ Add note"}</span>
        </button>
      </div>

      {/* Add-note form */}
      {formOpen && (
        <form
          onSubmit={saveNote}
          className="flex flex-row flex-wrap items-end gap-3 rounded-[10px] p-4"
          style={{ background: "var(--panel)", border: "1px solid var(--line)" }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Date</span>
            <input
              type="date"
              value={formDate}
              onChange={(e) => setFormDate(e.target.value)}
              className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
            />
          </label>
          <label className="flex flex-col gap-1 flex-1 min-w-[220px]">
            <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Note</span>
            <input
              type="text"
              value={formNote}
              onChange={(e) => setFormNote(e.target.value)}
              placeholder="What happened, or what to watch for?"
              className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
            />
          </label>
          <button
            type="submit"
            className="rounded-[8px] px-[16px] py-[8px] text-[12.5px] font-bold shrink-0"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            Save note
          </button>
        </form>
      )}

      {/* Day-of-week header row */}
      <div className="grid grid-cols-7 gap-2">
        {DAY_LABELS.map((label) => (
          <div key={label} className="text-center text-[11px] font-semibold uppercase pb-1" style={{ letterSpacing: "0.1em", color: "var(--text-mute)" }}>
            {label}
          </div>
        ))}
      </div>

      {/* Calendar grid */}
      <div className="grid grid-cols-7 gap-2">
        {Array.from({ length: leadingBlanks }, (_, i) => (
          <div key={`blank-${i}`} aria-hidden="true" />
        ))}
        {dayNumbers.map((day) => {
          const isToday = isTodayVisible && day === today.day;
          const entry = noteByDay.get(day);
          return (
            <div
              key={day}
              className={cn("flex flex-col justify-between aspect-square p-2 transition-colors duration-150 group relative")}
              style={{ background: "var(--panel)", border: `1px solid ${isToday ? "var(--gold)" : "var(--line)"}`, borderRadius: "var(--radius)" }}
              title={entry?.note}
            >
              <span className="text-[12px] font-semibold" style={{ fontFamily: "var(--font-mono)", color: isToday ? "var(--gold)" : "var(--text-dim)" }}>
                {day}
              </span>
              {entry && (
                <div className="flex flex-row items-center justify-between gap-1">
                  <span className="text-[10px] leading-tight line-clamp-2" style={{ color: "var(--text-mute)" }}>
                    {entry.note}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeNote(entry.date)}
                    aria-label={`Remove note for ${entry.date}`}
                    className="opacity-0 group-hover:opacity-100 transition-opacity text-[10px] shrink-0"
                    style={{ color: "var(--text-mute)" }}
                  >
                    ×
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default Standing;
