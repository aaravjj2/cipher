"""Company and this-week context for ranked option setups.

This is a read-only research layer. It uses Tradier market-data endpoints and
public headline feeds, and it does not touch account, trading, or order APIs.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SETUP_DIR = DATA / "setup_research"
OUT_DIR = DATA / "company_research"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tradier_stream_capture as tradier  # noqa: E402


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_setup_report(setup_dir: Path) -> Path:
    reports = sorted(setup_dir.glob("setup_research_*.json"), reverse=True)
    for path in reports:
        try:
            if load_json(path).get("ranked"):
                return path
        except (OSError, json.JSONDecodeError):
            continue
    raise FileNotFoundError(f"No non-empty setup research report found under {setup_dir}")


def week_start(today: date | None = None) -> date:
    today = today or datetime.now().date()
    return today - timedelta(days=today.weekday())


def tradier_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    token, _ = tradier.load_credentials("production")
    url = f"https://api.tradier.com/v1{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def quote_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    payload = tradier_get("/markets/quotes", {"symbols": ",".join(symbols), "greeks": "false"})
    quotes = (payload.get("quotes") or {}).get("quote") or []
    if isinstance(quotes, dict):
        quotes = [quotes]
    return {str(row.get("symbol") or "").upper(): row for row in quotes}


def history(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    payload = tradier_get(
        "/markets/history",
        {"symbol": symbol, "interval": "daily", "start": start.isoformat(), "end": end.isoformat()},
    )
    days = ((payload.get("history") or {}).get("day")) or []
    if isinstance(days, dict):
        days = [days]
    return list(days)


def to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def week_stats(symbol: str) -> dict[str, Any]:
    start = week_start()
    end = datetime.now().date()
    try:
        bars = history(symbol, start, end)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, TimeoutError, OSError) as exc:
        return {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(), "error": str(exc)}
    if not bars:
        return {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(), "error": "no_history"}
    first_open = to_float(bars[0].get("open"))
    last_close = to_float(bars[-1].get("close"))
    highs = [to_float(bar.get("high")) for bar in bars]
    lows = [to_float(bar.get("low")) for bar in bars]
    volume = sum(int(to_float(bar.get("volume")) or 0) for bar in bars)
    week_return = ((last_close - first_open) / first_open * 100.0) if first_open and last_close else None
    return {
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bars": len(bars),
        "week_open": first_open,
        "week_close": last_close,
        "week_high": max(v for v in highs if v is not None) if any(v is not None for v in highs) else None,
        "week_low": min(v for v in lows if v is not None) if any(v is not None for v in lows) else None,
        "week_return_pct": round(week_return, 3) if week_return is not None else None,
        "week_volume": volume,
    }


def yahoo_rss_headlines(symbol: str, limit: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"s": symbol, "region": "US", "lang": "en-US"})
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "CipherLocalResearch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if title:
            out.append({"title": title, "link": link, "published": published})
    return out


def direction_alignment(direction: str, quote: dict[str, Any], week: dict[str, Any], headlines: list[dict[str, str]]) -> tuple[str, list[str]]:
    notes: list[str] = []
    change_pct = to_float(quote.get("change_percentage"))
    week_return = to_float(week.get("week_return_pct"))
    if change_pct is not None:
        notes.append(f"today_{change_pct:+.2f}%")
    if week_return is not None:
        notes.append(f"week_{week_return:+.2f}%")
    if headlines:
        notes.append(f"{len(headlines)}_recent_headlines")
    aligned_today = (direction == "up" and change_pct is not None and change_pct > 0) or (
        direction == "down" and change_pct is not None and change_pct < 0
    )
    aligned_week = (direction == "up" and week_return is not None and week_return > 0) or (
        direction == "down" and week_return is not None and week_return < 0
    )
    if aligned_today and aligned_week:
        return "confirmed", notes
    if aligned_today or aligned_week:
        return "mixed", notes
    return "against", notes


def build_report(setup_path: Path, top: int, headline_limit: int) -> dict[str, Any]:
    setup_report = load_json(setup_path)
    setups = list(setup_report.get("ranked") or [])[:top]
    tickers = [str(row.get("ticker") or "").upper() for row in setups if row.get("ticker")]
    quotes = quote_batch(tickers)
    rows = []
    for setup in setups:
        ticker = str(setup.get("ticker") or "").upper()
        quote = quotes.get(ticker) or {}
        week = week_stats(ticker)
        headlines = yahoo_rss_headlines(ticker, headline_limit)
        alignment, notes = direction_alignment(str(setup.get("direction") or ""), quote, week, headlines)
        rows.append({
            "ticker": ticker,
            "setup_score": setup.get("score"),
            "grade": setup.get("grade"),
            "direction": setup.get("direction"),
            "setup": setup.get("setup"),
            "target": setup.get("target"),
            "last": to_float(quote.get("last")),
            "bid": to_float(quote.get("bid")),
            "ask": to_float(quote.get("ask")),
            "today_change_pct": to_float(quote.get("change_percentage")),
            "volume": quote.get("volume"),
            "week": week,
            "alignment": alignment,
            "company_notes": notes,
            "headlines": headlines,
            "research_caveat": "Company context is a research filter, not a buy/sell signal.",
        })
    return {
        "generated_at": now_utc(),
        "setup_source": str(setup_path),
        "headline_source": "Yahoo Finance RSS",
        "market_data_source": "Tradier production market data",
        "read_only": True,
        "rows": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Company And This-Week Setup Research",
        "",
        f"Generated: {report['generated_at']}",
        f"Setup source: `{report['setup_source']}`",
        "",
        "| Rank | Ticker | Setup | Score | Direction | Last | Today | Week | Alignment | Key Headlines |",
        "|---:|---|---|---:|---|---:|---:|---:|---|---|",
    ]
    for idx, row in enumerate(report["rows"], start=1):
        week_return = (row.get("week") or {}).get("week_return_pct")
        headlines = "; ".join(item["title"] for item in (row.get("headlines") or [])[:2])
        lines.append(
            f"| {idx} | {row['ticker']} | {row.get('setup')} | {row.get('setup_score')} | "
            f"{row.get('direction')} | {row.get('last')} | {row.get('today_change_pct')} | "
            f"{week_return} | {row.get('alignment')} | {headlines} |"
        )
    lines += [
        "",
        "Research-only caveat: company/news/week context is a filter for manual review, not financial advice or an order signal.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"company_research_{stamp}.json"
    csv_path = out_dir / f"company_research_{stamp}.csv"
    md_path = out_dir / f"company_research_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "ticker", "grade", "setup_score", "direction", "setup", "target",
            "last", "bid", "ask", "today_change_pct", "volume", "alignment",
            "company_notes", "week", "headlines",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({
                field: json.dumps(row.get(field), default=str) if isinstance(row.get(field), (list, dict)) else row.get(field)
                for field in fields
            })
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich top setups with company and this-week context.")
    parser.add_argument("--setup-report", type=Path)
    parser.add_argument("--setup-dir", type=Path, default=SETUP_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--headlines", type=int, default=5)
    args = parser.parse_args()

    setup_path = args.setup_report or latest_setup_report(args.setup_dir)
    report = build_report(setup_path, args.top, args.headlines)
    paths = write_outputs(report, args.out_dir)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "setup_source": report["setup_source"],
        "paths": paths,
        "top": [
            {
                "ticker": row["ticker"],
                "alignment": row["alignment"],
                "today_change_pct": row["today_change_pct"],
                "week_return_pct": (row["week"] or {}).get("week_return_pct"),
                "headline_count": len(row["headlines"]),
            }
            for row in report["rows"][:12]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
