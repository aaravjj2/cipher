"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import {
  ApiError,
  fetchBioFlowJob,
  fetchContractSearch,
  fetchFlow,
  startBioFlowJob,
  type ContractSearchResult,
  type FlowFilters,
  type RealFlowPrint,
} from "@/lib/api";
import type { SpyglassRow } from "@/types/cipher";

const FLOW_REFRESH_MS = 20_000;
const POLL_MS = 1500;

function printToRow(p: RealFlowPrint): SpyglassRow {
  return {
    ticker: p.ticker,
    timeEt: new Date(p.time).toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    }),
    sizePrem: p.premium,
    contracts: p.size,
    px: p.price,
    strike: p.strike,
    expiration: new Date(`${p.expiration}T00:00:00Z`).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    }),
    callPut: p.type === "call" ? "C" : "P",
    // Confirmed against the real site: this column shows which side the trade printed at
    // ("BID" = hit the bid / aggressive sell, "ASK" = lifted the ask / aggressive buy),
    // not a bid/ask price pair — that was a real bug in the first pass.
    bidAsk: p.side === "buy" ? "ASK" : "BID",
    pctOtm: Math.round(p.otm_pct * 10) / 10,
  };
}

/**
 * Spyglass panel — one component covering all 3 sub-views (Spyglass / Bio / Contract
 * Search) switched via internal tab state, per docs/research/components/spyglass.spec.md.
 * Bio and Contract Search are NOT separate sidebar items — selecting them only changes
 * this component's internal view, never Sidebar's `activePanel`.
 *
 * Header wiring (future page.tsx integration — NOT done here, out of this task's scope):
 * spec's `header.spec.md` says Spyglass's Header `rightSlot` should host the "Bio" /
 * "Contract Search" tab buttons, exported below as `SpyglassHeaderTabs`. Header.tsx has no
 * knowledge of Spyglass, so the active-tab state needs to be lifted the same way
 * Sidebar.tsx's `mobileOpen` is (controlled/uncontrolled pair of props) — a future
 * page.tsx would own the state and wire both sides to it, e.g.:
 *
 *   const [spyglassTab, setSpyglassTab] = useState<SpyglassTab>("spyglass");
 *   <Header
 *     panelName="Spyglass"
 *     rightSlot={<SpyglassHeaderTabs activeTab={spyglassTab} onChange={setSpyglassTab} />}
 *     ...
 *   />
 *   <Spyglass activeTab={spyglassTab} onActiveTabChange={setSpyglassTab} />
 *
 * When `activeTab`/`onActiveTabChange` are omitted (unwired/standalone use), Spyglass
 * falls back to fully internal tab state and renders its own inline tab bar at the top of
 * the panel body so it still works on its own.
 */

export type SpyglassTab = "spyglass" | "bio" | "contractSearch";

const TAB_LABELS: Record<SpyglassTab, string> = {
  spyglass: "Spyglass",
  bio: "Bio",
  contractSearch: "Contract Search",
};

/**
 * Exported so a future page.tsx can render these two buttons into Header's `rightSlot`
 * (see file header comment). Deliberately renders only "Bio" and "Contract Search" — the
 * base "Spyglass" view has no button of its own in the reference screenshots, it's the
 * implicit default/back state.
 */
export function SpyglassHeaderTabs({
  activeTab,
  onChange,
}: {
  activeTab: SpyglassTab;
  onChange: (tab: SpyglassTab) => void;
}) {
  const tabs: SpyglassTab[] = ["bio", "contractSearch"];
  return (
    <>
      {tabs.map((tab) => {
        const active = activeTab === tab;
        return (
          <button
            key={tab}
            type="button"
            onClick={() => onChange(tab)}
            aria-pressed={active}
            className="rounded-[8px] px-[14px] py-2 text-[12px] font-semibold whitespace-nowrap shrink-0"
            style={{
              border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
              color: active ? "var(--accent)" : "var(--text-dim)",
            }}
          >
            {TAB_LABELS[tab]}
          </button>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Shared toolbar primitives (mirrors StrikeMatrix.tsx's PillGroup convention:
// bordered pills, active = filled `--nav-active`)
// ---------------------------------------------------------------------------

function PillGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div
      className="flex flex-row items-center gap-[2px] rounded-[8px] p-[2px] shrink-0"
      style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className="rounded-[6px] px-[10px] py-[5px] text-[12px] font-semibold whitespace-nowrap transition-colors duration-150"
            style={{
              background: active ? "var(--nav-active)" : "transparent",
              color: active ? "var(--text)" : "var(--text-dim)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function ToolbarButton({
  children,
  onClick,
}: {
  children: React.ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[8px] px-[12px] py-[7px] text-[12px] font-semibold whitespace-nowrap shrink-0"
      style={{
        background: "var(--panel-2)",
        border: "1px solid var(--line)",
        color: "var(--text-dim)",
        fontFamily: "var(--font-mono)",
      }}
    >
      {children}
    </button>
  );
}

/**
 * The tape's own timestamp, in the slot the dead "Trade Date" picker used to occupy.
 *
 * core/app.py's flow() takes no date argument — it derives the tape from the *latest*
 * chain snapshot, and there is no historical options store behind it. So a date input
 * here could never filter anything: it accepted a date, changed nothing, and left the
 * reader believing they were looking at that session. What a tape reader actually needs
 * from this corner is how stale the prints are, which is a number the response really
 * carries.
 *
 * The date picker survives only in Contract Search, where the backend does accept it.
 */
function AsOfField({ asOf }: { asOf: string }) {
  const ms = Date.parse(asOf);
  const shown =
    asOf && !Number.isNaN(ms)
      ? new Date(ms).toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
        })
      : "—";
  return (
    <div className="flex flex-col gap-1 shrink-0">
      <span
        className="text-[10px] font-bold uppercase"
        style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}
      >
        Snapshot
      </span>
      <span
        className="rounded-[8px] px-[10px] py-[6px] text-[12px]"
        style={{
          background: "var(--panel-2)",
          border: "1px solid var(--line)",
          color: asOf ? "var(--text)" : "var(--text-mute)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {shown}
      </span>
    </div>
  );
}

function DateField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1 shrink-0">
      <span
        className="text-[10px] font-bold uppercase"
        style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}
      >
        Trade Date
      </span>
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-[8px] px-[10px] py-[6px] text-[12px] outline-none"
        style={{
          background: "var(--panel-2)",
          border: "1px solid var(--line)",
          color: "var(--text)",
          fontFamily: "var(--font-mono)",
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter option sets (labels/order taken from the reference screenshots; the
// shared size-bucket group is specced identically for Spyglass + Bio in
// spyglass.spec.md even though the Bio screenshot only shows 4 of the 5 pills —
// implemented per the written spec, the source of truth)
// ---------------------------------------------------------------------------

type PremiumBucket = "50" | "25" | "10" | "all";
const PREMIUM_OPTIONS: { label: string; value: PremiumBucket }[] = [
  { label: "≤$0.50", value: "50" },
  { label: "≤$0.25", value: "25" },
  { label: "≤$0.10", value: "10" },
  { label: "All px", value: "all" },
];

type OtmBucket = "5" | "10" | "25" | "50";
const OTM_OPTIONS: { label: string; value: OtmBucket }[] = [
  { label: "≥5% OTM", value: "5" },
  { label: "≥10%", value: "10" },
  { label: "≥25%", value: "25" },
  { label: "≥50%", value: "50" },
];

type SizeBucket = "5k" | "10k" | "25k" | "50k" | "100k";
const SIZE_OPTIONS: { label: string; value: SizeBucket }[] = [
  { label: "$5k", value: "5k" },
  { label: "$10k", value: "10k" },
  { label: "$25k", value: "25k" },
  { label: "$50k", value: "50k" },
  { label: "$100k", value: "100k" },
];

type CallPutFilter = "all" | "calls" | "puts";
const CALL_PUT_OPTIONS: { label: string; value: CallPutFilter }[] = [
  { label: "All", value: "all" },
  { label: "Calls", value: "calls" },
  { label: "Puts", value: "puts" },
];

type BidAskFilter = "bidAsk" | "ask" | "bid";
const BID_ASK_OPTIONS: { label: string; value: BidAskFilter }[] = [
  { label: "Bid/Ask", value: "bidAsk" },
  { label: "Ask", value: "ask" },
  { label: "Bid", value: "bid" },
];

type MoneynessFilter = "moneyness" | "otm" | "itm";
const MONEYNESS_OPTIONS: { label: string; value: MoneynessFilter }[] = [
  { label: "Moneyness", value: "moneyness" },
  { label: "OTM", value: "otm" },
  { label: "ITM", value: "itm" },
];

const TABLE_HEADERS = [
  "TICKER",
  "TIME ET",
  "SIZE (PREM)",
  "CONTRACTS",
  "PX",
  "STRIKE",
  "EXPIRATION",
  "C/P",
  "BID/ASK",
  "%OTM",
];

// ---------------------------------------------------------------------------
// Spyglass / Bio shared table shape
// ---------------------------------------------------------------------------

function SpyglassTable({
  rows,
  loadingText,
  isError = false,
}: {
  rows: SpyglassRow[];
  loadingText: string;
  isError?: boolean;
}) {
  return (
    <div className="rounded-[10px] overflow-x-auto" style={{ border: "1px solid var(--line)" }}>
      <table className="w-full min-w-[860px] border-collapse" style={{ fontFamily: "var(--font-mono)" }}>
        <thead>
          <tr>
            {TABLE_HEADERS.map((header, i) => {
              // Default sort is by Size (Prem) descending — the only sort the real site
              // visibly exposes (confirmed via screenshot: a highlighted "▼" on this column).
              const isSortColumn = header === "SIZE (PREM)";
              return (
                <th
                  key={header}
                  className={cn("text-[11px] font-bold uppercase px-3 py-[10px] whitespace-nowrap", i === 0 ? "text-left" : "text-right")}
                  style={{
                    letterSpacing: "0.06em",
                    color: isSortColumn ? "var(--accent)" : "var(--text-mute)",
                    borderBottom: "1px solid var(--line)",
                  }}
                >
                  {header}
                  {isSortColumn && <span style={{ marginLeft: "3px" }}>▼</span>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={TABLE_HEADERS.length}
                className="text-center py-16 text-[13px]"
                style={{ color: isError ? "var(--neg)" : "var(--text-dim)" }}
              >
                {loadingText}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={`${row.ticker}-${row.timeEt}-${i}`}>
                <td className="text-left px-3 py-[9px] text-[12px] font-semibold" style={{ color: "var(--text)" }}>
                  {row.ticker}
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {row.timeEt}
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]">
                  <span
                    className="inline-block rounded-[4px] px-2 py-[3px] font-semibold"
                    style={{ background: "color-mix(in srgb, var(--accent) 16%, transparent)", color: "var(--text)" }}
                  >
                    ${row.sizePrem.toLocaleString()}
                  </span>
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {row.contracts}
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {row.px.toFixed(2)}
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {row.strike}
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {row.expiration}
                </td>
                <td
                  className="text-right px-3 py-[9px] text-[12px] font-semibold"
                  style={{ color: row.callPut === "C" ? "var(--accent)" : "var(--neg)" }}
                >
                  {row.callPut}
                </td>
                <td
                  className="text-right px-3 py-[9px] text-[12px] font-semibold"
                  style={{ color: row.bidAsk === "ASK" ? "var(--accent)" : "var(--neg)" }}
                >
                  {row.bidAsk}
                </td>
                <td className="text-right px-3 py-[9px] text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {row.pctOtm}%
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spyglass sub-view
// ---------------------------------------------------------------------------

const SIZE_TO_MIN_PREMIUM: Record<SizeBucket, number> = {
  "5k": 5_000,
  "10k": 10_000,
  "25k": 25_000,
  "50k": 50_000,
  "100k": 100_000,
};

const PREMIUM_TO_MAX_PRICE: Record<PremiumBucket, number | undefined> = {
  "50": 0.5,
  "25": 0.25,
  "10": 0.1,
  all: undefined,
};

function SpyglassView({ ticker }: { ticker: string }) {
  // "All px", not the ≤$0.50 contract-price cap. The two price filters compose:
  // maxPrice caps the per-contract price while minPremium floors the notional, so
  // the old default asked for prints of ≤$0.50 options worth ≥$5,000 — 100+
  // contracts of a near-worthless strike. Measured on AAPL: 400 prints with no
  // cap, 0 with pmax=0.5. The panel opened on an empty table every single time.
  const [premium, setPremium] = useState<PremiumBucket>("all");
  const [size, setSize] = useState<SizeBucket>("5k");
  const [callPut, setCallPut] = useState<CallPutFilter>("all");
  const [bidAsk, setBidAsk] = useState<BidAskFilter>("bidAsk");
  const [moneyness, setMoneyness] = useState<MoneynessFilter>("moneyness");

  const [prints, setPrints] = useState<RealFlowPrint[]>([]);
  const [asOf, setAsOf] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState("");

  const filters: FlowFilters = useMemo(
    () => ({
      minPremium: SIZE_TO_MIN_PREMIUM[size],
      maxPrice: PREMIUM_TO_MAX_PRICE[premium],
      optionType: callPut === "calls" ? "call" : callPut === "puts" ? "put" : "all",
      side: bidAsk === "ask" ? "buy" : bidAsk === "bid" ? "sell" : "all",
      moneyness: moneyness === "otm" ? "otm" : moneyness === "itm" ? "itm" : "all",
    }),
    [size, premium, callPut, bidAsk, moneyness]
  );

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setStatus((prev) => (prev === "ready" ? prev : "loading"));
      try {
        const res = await fetchFlow(ticker, filters, signal);
        setPrints(res.prints);
        setAsOf(res.as_of);
        setStatus("ready");
        setErrorMessage("");
      } catch (err) {
        if (signal?.aborted) return;
        setStatus("error");
        setErrorMessage(err instanceof ApiError ? err.message : "Failed to load options flow.");
      }
    },
    [ticker, filters]
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() => load(controller.signal));
    const interval = setInterval(() => load(), FLOW_REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [load]);

  // Default sort matches the real site: largest premium first (confirmed via screenshot —
  // the API doesn't return prints pre-sorted, so this is a real client-side sort, not cosmetic).
  const rows = useMemo(
    () => prints.map(printToRow).sort((a, b) => b.sizePrem - a.sizePrem),
    [prints]
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-row flex-wrap items-end gap-2">
        <AsOfField asOf={asOf} />
        <PillGroup options={PREMIUM_OPTIONS} value={premium} onChange={setPremium} />
        <PillGroup options={SIZE_OPTIONS} value={size} onChange={setSize} />
        <PillGroup options={CALL_PUT_OPTIONS} value={callPut} onChange={setCallPut} />
        <PillGroup options={BID_ASK_OPTIONS} value={bidAsk} onChange={setBidAsk} />
        <PillGroup options={MONEYNESS_OPTIONS} value={moneyness} onChange={setMoneyness} />
      </div>

      {/* Real distinct-expiration count from the current print set — confirmed against the
          real site's "Expirations (N/N)" label. No per-expiration filter is wired (the
          real site's exact dropdown behavior wasn't captured), so this stays a count only. */}
      <div className="flex flex-row flex-wrap items-center justify-between gap-2">
        <ToolbarButton>Expirations ({new Set(rows.map((r) => r.expiration)).size}/{new Set(rows.map((r) => r.expiration)).size})</ToolbarButton>
        {status === "ready" && (
          <span className="text-[11px]" style={{ color: "var(--text-mute)" }}>
            {rows.length} print{rows.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <SpyglassTable
        rows={status === "ready" ? rows : []}
        isError={status === "error"}
        loadingText={
          status === "error"
            ? errorMessage
            : status === "loading"
              ? `Scanning ${ticker}...`
              : `No prints matched the current filters for ${ticker}.`
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bio sub-view
// ---------------------------------------------------------------------------

const OTM_THRESHOLD: Record<OtmBucket, number> = { "5": 5, "10": 10, "25": 25, "50": 50 };

function BioView() {
  const [otm, setOtm] = useState<OtmBucket>("5");
  const [size, setSize] = useState<SizeBucket>("5k");
  const [callPut, setCallPut] = useState<CallPutFilter>("all");

  const [prints, setPrints] = useState<RealFlowPrint[]>([]);
  const [asOf, setAsOf] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [progressText, setProgressText] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Real bulk scan across core/app.py's BIO_UNIVERSE (~90s for 54 tickers) — manually
  // triggered via Rescan rather than auto-loading on mount, since it's a genuinely
  // expensive sequential scan, not a single-ticker fetch like the main Spyglass view.
  function rescan() {
    if (status === "loading") return;
    if (pollRef.current) clearInterval(pollRef.current);
    setStatus("loading");
    setErrorMessage("");
    setProgressText("Scanning pharma, biotech & medtech… this can take a moment.");

    startBioFlowJob({
      minPremium: SIZE_TO_MIN_PREMIUM[size],
      optionType: callPut === "calls" ? "call" : callPut === "puts" ? "put" : "all",
      moneyness: "otm",
    })
      .then(({ job_id }) => {
        pollRef.current = setInterval(async () => {
          try {
            const j = await fetchBioFlowJob(job_id);
            if (j.status === "done") {
              if (pollRef.current) clearInterval(pollRef.current);
              setPrints(j.result?.prints ?? []);
              setAsOf(j.result?.as_of ?? "");
              setStatus("ready");
            } else if (j.status === "error") {
              if (pollRef.current) clearInterval(pollRef.current);
              setErrorMessage(j.error || "Bio scan failed.");
              setStatus("error");
            } else {
              setProgressText(j.message || "Scanning…");
            }
          } catch (err) {
            if (pollRef.current) clearInterval(pollRef.current);
            setErrorMessage(err instanceof ApiError ? err.message : "Lost connection while scanning.");
            setStatus("error");
          }
        }, POLL_MS);
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : "Failed to start Bio scan.");
        setStatus("error");
      });
  }

  const threshold = OTM_THRESHOLD[otm];
  const rows = useMemo(
    () =>
      prints
        .filter((p) => Math.abs(p.otm_pct) >= threshold)
        .sort((a, b) => b.premium - a.premium)
        .map(printToRow),
    [prints, threshold]
  );

  const loadingText =
    status === "error"
      ? errorMessage
      : status === "loading"
        ? progressText
        : status === "idle"
          ? "Click Rescan to scan pharma, biotech & medtech names for real options flow."
          : rows.length === 0
            ? "No prints matched the current filters."
            : "";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-row flex-wrap items-end gap-2">
        <AsOfField asOf={asOf} />
        <PillGroup options={OTM_OPTIONS} value={otm} onChange={setOtm} />
        <PillGroup options={SIZE_OPTIONS} value={size} onChange={setSize} />
        <PillGroup options={CALL_PUT_OPTIONS} value={callPut} onChange={setCallPut} />
        <ToolbarButton onClick={rescan}>{status === "loading" ? "Scanning…" : "Rescan"}</ToolbarButton>
      </div>

      <SpyglassTable rows={status === "ready" ? rows : []} isError={status === "error"} loadingText={loadingText} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contract Search sub-view
// ---------------------------------------------------------------------------

function ContractSearchView({ defaultTicker }: { defaultTicker: string }) {
  const [ticker, setTicker] = useState(defaultTicker);
  const [strike, setStrike] = useState("");
  const [callPut, setCallPut] = useState<"call" | "put">("call");
  const [tradeDate, setTradeDate] = useState("");
  const [result, setResult] = useState<ContractSearchResult | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async () => {
    if (!ticker.trim() || !strike.trim()) return;
    setStatus("loading");
    setError(null);
    try {
      const res = await fetchContractSearch({
        ticker: ticker.trim().toUpperCase(),
        strike: strike.trim(),
        type: callPut,
        date: tradeDate || undefined,
      });
      setResult(res);
      setStatus("ready");
    } catch (err) {
      setError((err as Error)?.message || "contract search failed");
      setStatus("error");
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-row flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>
            Ticker
          </span>
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="AAPL"
            className="w-[140px] rounded-[8px] px-[10px] py-[7px] text-[13px] font-semibold uppercase outline-none"
            style={{
              background: "var(--panel-2)",
              border: "1px solid var(--line)",
              color: "var(--text)",
              fontFamily: "var(--font-mono)",
            }}
          />
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase" style={{ letterSpacing: "0.12em", color: "var(--text-mute)" }}>
            Strike
          </span>
          <input
            type="number"
            value={strike}
            onChange={(e) => setStrike(e.target.value)}
            placeholder="200"
            className="w-[110px] rounded-[8px] px-[10px] py-[7px] text-[13px] outline-none"
            style={{
              background: "var(--panel-2)",
              border: "1px solid var(--line)",
              color: "var(--text)",
              fontFamily: "var(--font-mono)",
            }}
          />
        </div>

        <PillGroup
          options={[
            { label: "Call", value: "call" as const },
            { label: "Put", value: "put" as const },
          ]}
          value={callPut}
          onChange={setCallPut}
        />

        <DateField value={tradeDate} onChange={setTradeDate} />

        <button
          type="button"
          onClick={handleSearch}
          className="rounded-[8px] px-[18px] py-[7px] text-[13px] font-semibold shrink-0"
          style={{ background: "transparent", border: "1px solid var(--accent)", color: "var(--accent)" }}
        >
          Search
        </button>
      </div>

      {status === "idle" && (
        <div className="text-center py-16 text-[13px]" style={{ color: "var(--text-mute)" }}>
          Type a ticker, a strike, and pick call or put to see today&apos;s traded volume and how much was bought vs sold.
        </div>
      )}

      {status === "loading" && (
        <div className="text-center py-16 text-[13px]" style={{ color: "var(--text-mute)" }}>
          Reading the trade tape…
        </div>
      )}

      {status === "error" && (
        <div className="text-center py-16 text-[13px]" style={{ color: "var(--neg)" }}>
          {error}
        </div>
      )}

      {status === "ready" && result && !result.found && (
        <div className="text-center py-12 text-[13px]" style={{ color: "var(--text-mute)" }}>
          <div>{result.message}</div>
          {!!result.nearest_strikes?.length && (
            <div className="mt-2 text-[12px]">
              Nearest listed strikes: {result.nearest_strikes.join(", ")}
            </div>
          )}
        </div>
      )}

      {status === "ready" && result?.found && <ContractSearchResultView result={result} />}
    </div>
  );
}

/** Results for one contract: the day's volume split into bought vs sold, plus the tape. */
function ContractSearchResultView({ result }: { result: ContractSearchResult }) {
  const buyPct = result.buy_pct ?? 0;
  const sellPct = result.volume ? (100 * (result.sell_volume ?? 0)) / result.volume : 0;
  const num = (v: number | null | undefined, digits = 0) =>
    v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: digits });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-row flex-wrap items-baseline gap-x-4 gap-y-1 text-[12px]" style={{ color: "var(--text-dim)" }}>
        <span className="text-[13px] font-bold" style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>
          {result.symbol}
        </span>
        <span>exp {result.expiration}</span>
        <span>{result.trade_date}</span>
        <span>OI {num(result.open_interest)}</span>
        <span>bid {result.bid ?? "—"} / ask {result.ask ?? "—"}</span>
        <span>VWAP {result.vwap ?? "—"}</span>
      </div>

      {/* Bought vs sold, as a single proportional bar. */}
      <div className="flex flex-col gap-1">
        <div className="flex flex-row justify-between text-[11px]" style={{ color: "var(--text-mute)" }}>
          <span>
            BOUGHT {num(result.buy_volume)} ({buyPct.toFixed(1)}%)
          </span>
          <span>
            SOLD {num(result.sell_volume)} ({sellPct.toFixed(1)}%)
          </span>
        </div>
        <div className="flex h-[10px] w-full overflow-hidden rounded-[5px]" style={{ background: "var(--panel-2)" }}>
          <div style={{ width: `${buyPct}%`, background: "var(--accent)" }} />
          <div style={{ width: `${sellPct}%`, background: "var(--neg)" }} />
        </div>
        <div className="flex flex-row flex-wrap gap-x-4 text-[11px]" style={{ color: "var(--text-mute)" }}>
          <span>{num(result.trades)} trades</span>
          <span>volume {num(result.volume)}</span>
          <span>premium ${num(result.premium)}</span>
          {!!result.unclassified_volume && <span>{num(result.unclassified_volume)} unclassified</span>}
        </div>
      </div>

      {/* The inference is stated, not buried — side is not exchange-reported. */}
      <div className="text-[10px] leading-snug" style={{ color: "var(--text-mute)" }}>
        {result.caveat}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-[12px] tabular-nums">
          <thead>
            <tr style={{ color: "var(--text-mute)" }}>
              {["TIME", "SIZE", "PRICE", "PREMIUM", "SIDE", "EXCH"].map((h) => (
                <th key={h} className="whitespace-nowrap border-b px-3 py-1 text-[10px] font-semibold" style={{ borderColor: "var(--line)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(result.largest_trades ?? []).map((t, i) => (
              <tr key={`${t.time}-${i}`} style={{ color: "var(--text-dim)" }}>
                <td className="whitespace-nowrap px-3 py-1">
                  {new Date(t.time).toLocaleTimeString("en-US", { hour12: false })}
                </td>
                <td className="whitespace-nowrap px-3 py-1">{num(t.size)}</td>
                <td className="whitespace-nowrap px-3 py-1">{t.price}</td>
                <td className="whitespace-nowrap px-3 py-1 font-semibold" style={{ color: "var(--text)" }}>
                  ${num(t.premium)}
                </td>
                <td
                  className="whitespace-nowrap px-3 py-1 font-semibold uppercase"
                  style={{ color: t.side === "buy" ? "var(--accent)" : t.side === "sell" ? "var(--neg)" : "var(--text-mute)" }}
                >
                  {t.side}
                </td>
                <td className="whitespace-nowrap px-3 py-1">{t.exchange}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type SpyglassProps = {
  /** Mock ticker used for the "Scanning {TICKER}..." loading copy on the Spyglass sub-view. */
  ticker?: string;
  /**
   * Controlled sub-view tab, for lifting state up to a future page.tsx that also drives
   * `SpyglassHeaderTabs` inside Header's `rightSlot` — see the file header comment. When
   * omitted, Spyglass manages its own tab state internally and renders a fallback inline
   * tab bar (mirrors Sidebar.tsx's controlled/uncontrolled `mobileOpen` convention).
   */
  activeTab?: SpyglassTab;
  onActiveTabChange?: (tab: SpyglassTab) => void;
};

export function Spyglass({ ticker = "AAPL", activeTab: activeTabProp, onActiveTabChange }: SpyglassProps = {}) {
  const [internalTab, setInternalTab] = useState<SpyglassTab>("spyglass");

  const isControlled = activeTabProp !== undefined;
  const activeTab = isControlled ? activeTabProp : internalTab;
  const setActiveTab = (tab: SpyglassTab) => {
    onActiveTabChange?.(tab);
    if (!isControlled) setInternalTab(tab);
  };

  const view = useMemo(() => {
    switch (activeTab) {
      case "bio":
        return <BioView />;
      case "contractSearch":
        return <ContractSearchView defaultTicker={ticker} />;
      case "spyglass":
      default:
        return <SpyglassView ticker={ticker} />;
    }
  }, [activeTab, ticker]);

  return (
    <section className="spyglass flex flex-col gap-3" style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
      {/* Fallback inline tab bar — only rendered when uncontrolled, since a controlled
          parent is expected to render SpyglassHeaderTabs inside Header's rightSlot instead
          (avoids a duplicate set of tabs once wired, same pattern as Sidebar's hamburger). */}
      {!isControlled && (
        <div className="flex flex-row flex-wrap items-center gap-2">
          <SpyglassHeaderTabs activeTab={activeTab} onChange={setActiveTab} />
        </div>
      )}

      {view}
    </section>
  );
}

export default Spyglass;
