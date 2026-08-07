"use client";

import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";
import { readLocal, writeLocal } from "@/lib/localStorage";

/**
 * Journal panel — a real, locally-persisted trading journal (date + P&L + note per day).
 * There's no server-side backend for this feature (confirmed against the legacy
 * vanilla-JS frontend, which stores journal entries the same way), so localStorage is the
 * real data source here, not a mock stand-in for one. Layout: docs/research/components/
 * journal.spec.md.
 */

const STORAGE_KEY = "cipher_journal_v1";
const DISPLAY_NAME = "Trader";

type JournalEntry = { date: string; pnl: number; note: string };

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

function formatSignedDollars(value: number): string {
  const sign = value < 0 ? "-" : "+";
  const abs = Math.round(Math.abs(value));
  return `${sign}$${abs.toLocaleString("en-US")}`;
}

function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
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

function DayPnlBadge({ pnl }: { pnl: number }) {
  const isProfit = pnl >= 0;
  return (
    <span
      className="self-start rounded-[4px] px-[5px] py-[2px] text-[10px] font-bold whitespace-nowrap"
      style={{
        fontFamily: "var(--font-mono)",
        letterSpacing: "0.02em",
        // Cipher convention: purple = profit, red = loss (NOT the conventional green/red).
        background: isProfit ? "color-mix(in srgb, var(--accent) 24%, transparent)" : "color-mix(in srgb, var(--neg) 20%, transparent)",
        color: isProfit ? "var(--accent)" : "var(--neg)",
      }}
    >
      {formatSignedDollars(pnl)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Journal() {
  // `today` starts null and is set inside an effect (client-only) so the build-time
  // static-export render and the client hydration never disagree on "today's" date.
  const [today, setToday] = useState<{ year: number; month: number; day: number } | null>(null);
  const [view, setView] = useState<{ year: number; month: number } | null>(null);
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [formDate, setFormDate] = useState("");
  const [formResult, setFormResult] = useState<"Profit" | "Loss">("Profit");
  const [formAmount, setFormAmount] = useState("");
  const [formNote, setFormNote] = useState("");

  useEffect(() => {
    const now = new Date();
    setToday({ year: now.getFullYear(), month: now.getMonth(), day: now.getDate() });
    setView({ year: now.getFullYear(), month: now.getMonth() });
    setFormDate(now.toISOString().slice(0, 10));
    setEntries(readLocal<JournalEntry[]>(STORAGE_KEY, []));
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

  function saveEntry(e: React.FormEvent) {
    e.preventDefault();
    const amount = Math.abs(Number(formAmount.replace(/[^0-9.-]/g, "")) || 0);
    const pnl = formResult === "Loss" ? -amount : amount;
    const next = [...entries.filter((en) => en.date !== formDate), { date: formDate, pnl, note: formNote }];
    setEntries(next);
    writeLocal(STORAGE_KEY, next);
    setFormAmount("");
    setFormNote("");
    setFormOpen(false);
  }

  function removeEntry(date: string) {
    const next = entries.filter((en) => en.date !== date);
    setEntries(next);
    writeLocal(STORAGE_KEY, next);
  }

  const isTodayVisible = Boolean(today && view && view.year === today.year && view.month === today.month);

  const pnlByDay = useMemo(() => {
    const map = new Map<number, JournalEntry>();
    if (!view) return map;
    for (const entry of entries) {
      const d = new Date(`${entry.date}T00:00:00`);
      if (d.getFullYear() === view.year && d.getMonth() === view.month) map.set(d.getDate(), entry);
    }
    return map;
  }, [entries, view]);

  const monthPnl = useMemo(() => Array.from(pnlByDay.values()).reduce((sum, e) => sum + e.pnl, 0), [pnlByDay]);

  if (!view || !today) {
    return (
      <section className="journal-panel flex items-center justify-center py-20" style={{ color: "var(--text-mute)" }}>
        Loading journal…
      </section>
    );
  }

  const leadingBlanks = firstWeekdayOfMonth(view.year, view.month);
  const totalDays = daysInMonth(view.year, view.month);
  const dayNumbers = Array.from({ length: totalDays }, (_, i) => i + 1);

  return (
    <section className="journal-panel flex flex-col gap-5" style={{ fontFamily: "var(--font-sans)", color: "var(--text)" }}>
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
          {DISPLAY_NAME}&rsquo;s Trading Journal
        </h1>
      </div>

      {/* Controls row */}
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

        <div className="flex flex-row items-center gap-3">
          <div className="flex flex-col items-center rounded-[12px] px-5 py-2" style={{ background: "linear-gradient(135deg, var(--accent), var(--ring))" }}>
            <span className="text-[9px] font-bold uppercase" style={{ letterSpacing: "0.14em", color: "rgba(255,255,255,0.8)", fontFamily: "var(--font-mono)" }}>
              Month P&amp;L
            </span>
            <span className="text-[20px] font-bold leading-tight" style={{ fontFamily: "var(--font-mono)", color: "#ffffff" }}>
              {formatSignedDollars(monthPnl)}
            </span>
          </div>

          <button
            type="button"
            onClick={() => setFormOpen((v) => !v)}
            className="flex flex-row items-center gap-[7px] rounded-[9px] px-[14px] py-[9px] text-[12.5px] font-bold"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
          >
            <span style={{ fontFamily: "var(--font-mono)" }}>{formOpen ? "Close" : "+ Add day"}</span>
          </button>
        </div>
      </div>

      {/* Add-entry form */}
      {formOpen && (
        <form
          onSubmit={saveEntry}
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
          <label className="flex flex-col gap-1">
            <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Result</span>
            <select
              value={formResult}
              onChange={(e) => setFormResult(e.target.value as "Profit" | "Loss")}
              className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
            >
              <option>Profit</option>
              <option>Loss</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Amount</span>
            <input
              type="text"
              value={formAmount}
              onChange={(e) => setFormAmount(e.target.value)}
              placeholder="$0"
              className="w-[110px] rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
            />
          </label>
          <label className="flex flex-col gap-1 flex-1 min-w-[160px]">
            <span className="text-[10.5px] font-bold uppercase" style={{ letterSpacing: "0.08em", color: "var(--text-mute)" }}>Notes</span>
            <input
              type="text"
              value={formNote}
              onChange={(e) => setFormNote(e.target.value)}
              placeholder="What did the setup teach you?"
              className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", fontFamily: "var(--font-mono)" }}
            />
          </label>
          <button
            type="submit"
            className="rounded-[8px] px-[16px] py-[8px] text-[12.5px] font-bold shrink-0"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            Save day
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
          const entry = pnlByDay.get(day);
          return (
            <div
              key={day}
              className={cn("flex flex-col justify-between aspect-square p-2 transition-colors duration-150 group relative")}
              style={{ background: "var(--panel)", border: `1px solid ${isToday ? "var(--gold)" : "var(--line)"}`, borderRadius: "var(--radius)" }}
            >
              <span className="text-[12px] font-semibold" style={{ fontFamily: "var(--font-mono)", color: isToday ? "var(--gold)" : "var(--text-dim)" }}>
                {day}
              </span>
              {entry && (
                <div className="flex flex-row items-center justify-between gap-1">
                  <DayPnlBadge pnl={entry.pnl} />
                  <button
                    type="button"
                    onClick={() => removeEntry(entry.date)}
                    aria-label={`Remove entry for ${entry.date}`}
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

export default Journal;
