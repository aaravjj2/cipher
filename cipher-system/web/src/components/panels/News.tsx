"use client";

import { useEffect, useState } from "react";
import { fetchNews, type RealNews } from "@/lib/api";

/**
 * News panel — the active symbol's headlines from Yahoo Finance's public RSS feed
 * (core/company_research_engine.py's yahoo_rss_headlines, already shipped and already
 * exercised by that module's CLI). Nothing is derived here.
 *
 * Deliberately *not* built: sentiment scores, relevance ranking, ML-derived signals,
 * alerting. Each of those would be a number Cipher asserts rather than reads, and none
 * of them could be traced back to a source the user can check — the same reason the
 * Standing and Holdings panels state their limits on screen. The backend's own `caveat`
 * string is rendered verbatim below rather than paraphrased, so the disclaimer the user
 * sees is the one the API actually sent.
 *
 * Ordering is the feed's, not ours. The list is not re-sorted, filtered, or deduplicated.
 */

/** Publisher shown per row. This is the link's own hostname — an observable fact about
 *  the URL, not a lookup — because Yahoo's RSS items carry no publisher field. */
function hostOf(link: string): string {
  try {
    return new URL(link).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/**
 * The feed's RFC-822 `published` string, rendered in the reader's local timezone.
 * If Date can't parse it, the raw string is shown as-is rather than a guess.
 */
function formatPublished(published: string): string {
  const ms = Date.parse(published);
  if (Number.isNaN(ms)) return published;
  return new Date(ms).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

type NewsProps = { ticker: string };

/** Result plus the symbol it belongs to. Tagging it is what lets a ticker change read as
 *  "loading" without an effect clearing state first — a result for the previous symbol
 *  simply stops matching, so it is never shown under the new one's heading. */
type Loaded = { ticker: string; data: RealNews | null; error: string | null };

export function News({ ticker }: NewsProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    // No polling: an RSS feed updates on the publisher's schedule, not a 15s one, and a
    // headline list has no live number in it to keep fresh. It reloads when the ticker
    // changes.
    const controller = new AbortController();
    fetchNews(ticker, 25, controller.signal)
      .then((res) => setLoaded({ ticker, data: res, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({
          ticker,
          data: null,
          error: err instanceof Error ? err.message : "Could not load headlines.",
        });
      });
    return () => controller.abort();
  }, [ticker]);

  const current = loaded?.ticker === ticker ? loaded : null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;
  const headlines = data?.headlines ?? [];

  return (
    <div className="flex flex-col gap-4">
      <section
        className="flex flex-col gap-3 rounded-[var(--radius)] p-5"
        style={{ background: "var(--panel)", border: "1px solid var(--line)" }}
      >
        <div className="flex flex-row flex-wrap items-center justify-between gap-3">
          <h2
            className="text-[13px] font-bold uppercase"
            style={{ letterSpacing: "0.06em", color: "var(--text)" }}
          >
            {ticker} Headlines
          </h2>
          <span
            className="text-[10px] font-bold uppercase"
            style={{ letterSpacing: "0.08em", color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}
          >
            {data ? `${headlines.length} from ${data.source}` : error ? "unavailable" : "loading…"}
          </span>
        </div>

        {error && (
          <p className="text-[12.5px]" style={{ color: "var(--neg)" }}>
            {error}
          </p>
        )}

        {!error && data && headlines.length === 0 && (
          <p className="text-[12.5px] italic" style={{ color: "var(--text-mute)" }}>
            The feed returned no items for {ticker}. That is the feed&apos;s answer, not a
            failure to load — Yahoo publishes nothing for some symbols.
          </p>
        )}

        {!error && !data && (
          <p className="text-[12.5px] italic" style={{ color: "var(--text-mute)" }}>
            Loading headlines…
          </p>
        )}

        {headlines.length > 0 && (
          <ul className="flex flex-col gap-2">
            {headlines.map((row, i) => {
              const host = hostOf(row.link);
              return (
                <li key={`${row.link}-${i}`}>
                  <a
                    href={row.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex flex-col gap-[6px] rounded-[10px] px-4 py-3 transition-colors hover:brightness-125"
                    style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
                  >
                    <span className="text-[13px] leading-snug" style={{ color: "var(--text)" }}>
                      {row.title}
                    </span>
                    <span
                      className="flex flex-row flex-wrap items-center gap-2 text-[10.5px]"
                      style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}
                    >
                      {host && <span>{host}</span>}
                      {host && row.published && <span aria-hidden>·</span>}
                      {row.published && <span>{formatPublished(row.published)}</span>}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {data && (
        <p
          className="text-[11.5px] leading-relaxed"
          style={{ color: "var(--text-mute)", fontFamily: "var(--font-mono)" }}
        >
          {data.caveat}
        </p>
      )}
    </div>
  );
}

export default News;
