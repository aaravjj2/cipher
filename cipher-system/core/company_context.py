"""Source-labelled company, filing, corporate-fact, and macro-event context."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "company_context"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_SOURCE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_2026 = ("2026-09-15", "2026-09-16", "2026-10-27", "2026-10-28", "2026-12-08", "2026-12-09")
FACTS = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    "net_income": ("NetIncomeLoss",), "assets": ("Assets",), "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquity",), "diluted_eps": ("EarningsPerShareDiluted",),
    "dividend_per_share": ("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _request(url: str, *, binary: bool = False) -> Any:
    agent = os.environ.get("SEC_USER_AGENT", "CipherLocalResearch/1.0 local-personal-research")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "application/json,text/calendar,*/*"}), timeout=20) as response:
        raw = response.read()
    return raw if binary else json.loads(raw.decode("utf-8"))


def _cached(name: str, url: str, *, ttl_hours: int = 24, binary: bool = False) -> Any:
    path = CACHE / name
    if path.is_file() and datetime.now().timestamp() - path.stat().st_mtime < ttl_hours * 3600:
        raw = path.read_bytes()
        return raw if binary else json.loads(raw)
    value = _request(url, binary=binary)
    CACHE.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if binary:
        temporary.write_bytes(value)
    else:
        temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)
    return value


def resolve_company(ticker: str, mapping: dict) -> dict | None:
    symbol = ticker.upper()
    for row in mapping.values():
        if str(row.get("ticker") or "").upper() == symbol:
            return {"ticker": symbol, "cik": str(row["cik_str"]).zfill(10), "name": row.get("title")}
    return None


def _latest_fact(facts: dict, tags: tuple[str, ...]) -> dict | None:
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    candidates = []
    for tag in tags:
        item = gaap.get(tag) or {}
        for unit, rows in (item.get("units") or {}).items():
            for row in rows:
                if row.get("form") in {"10-Q", "10-K", "20-F", "40-F"} and row.get("filed") and row.get("val") is not None:
                    candidates.append({"tag": tag, "label": item.get("label"), "unit": unit, **row})
    return max(candidates, key=lambda row: (row.get("filed", ""), row.get("end", "")), default=None)


def extract_fundamentals(facts: dict) -> list[dict]:
    return [{"name": name, **value} for name, tags in FACTS.items() if (value := _latest_fact(facts, tags))]


def extract_filings(submissions: dict, limit: int = 12) -> list[dict]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    rows = []
    for index, accession in enumerate(recent.get("accessionNumber") or []):
        form = _parallel(recent, "form", index)
        if form not in {"10-K", "10-Q", "8-K", "4", "DEF 14A"}:
            continue
        accession_plain = accession.replace("-", "")
        primary = _parallel(recent, "primaryDocument", index) or ""
        cik_plain = str(submissions.get("cik") or "").lstrip("0")
        rows.append({"form": form, "filed": _parallel(recent, "filingDate", index),
                     "report_date": _parallel(recent, "reportDate", index),
                     "accession": accession,
                     "url": f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{accession_plain}/{primary}"})
        if len(rows) >= limit:
            break
    return rows


def _parallel(mapping: dict, key: str, index: int) -> Any:
    values = mapping.get(key) or []
    return values[index] if index < len(values) else None


def parse_ics(raw: bytes, *, start: date, days: int = 45) -> list[dict]:
    text = raw.decode("utf-8", errors="replace").replace("\r\n ", "")
    events = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"\nSUMMARY:(.+)", block)
        stamp = re.search(r"\nDTSTART(?:;[^:]*)?:(\d{8})(?:T(\d{6}))?", block)
        if not summary or not stamp:
            continue
        event_date = datetime.strptime(stamp.group(1), "%Y%m%d").date()
        if start <= event_date <= start + timedelta(days=days):
            events.append({"date": event_date.isoformat(), "time": stamp.group(2), "title": summary.group(1).replace("\\,", ","),
                           "source": "U.S. Bureau of Labor Statistics", "source_url": BLS_ICS})
    return sorted(events, key=lambda row: (row["date"], row.get("time") or ""))


def macro_events(today: date | None = None) -> dict:
    current = today or date.today()
    errors = []
    try:
        bls = parse_ics(_cached("bls.ics", BLS_ICS, ttl_hours=6, binary=True), start=current)
    except Exception as exc:
        bls, errors = [], [{"source": "BLS", "error": str(exc)}]
    fomc = [{"date": day, "time": None, "title": "FOMC scheduled meeting day", "source": "Federal Reserve Board", "source_url": FOMC_SOURCE}
            for day in FOMC_2026 if current <= date.fromisoformat(day) <= current + timedelta(days=120)]
    return {"events": sorted(bls + fomc, key=lambda row: (row["date"], row.get("time") or "")), "errors": errors,
            "generated_at": _now(), "sources": [BLS_ICS, FOMC_SOURCE]}


def context(ticker: str) -> dict:
    symbol, errors = ticker.upper(), []
    company = submissions = facts = None
    try:
        company = resolve_company(symbol, _cached("sec_company_tickers.json", SEC_TICKERS, ttl_hours=24 * 7))
        if company:
            submissions = _cached(f"submissions_{company['cik']}.json", SEC_SUBMISSIONS.format(cik=company["cik"]), ttl_hours=6)
            facts = _cached(f"facts_{company['cik']}.json", SEC_FACTS.format(cik=company["cik"]), ttl_hours=6)
    except Exception as exc:
        errors.append({"source": "SEC EDGAR", "error": str(exc)})
    profile = None
    if company and submissions:
        profile = {**company, "name": submissions.get("name") or company.get("name"), "sic": submissions.get("sic"),
                   "sic_description": submissions.get("sicDescription"), "state": submissions.get("stateOfIncorporation"),
                   "fiscal_year_end": submissions.get("fiscalYearEnd"), "exchanges": submissions.get("exchanges") or [],
                   "website": submissions.get("website"), "investor_website": submissions.get("investorWebsite"),
                   "sec_company_url": f"https://www.sec.gov/edgar/browse/?CIK={company['cik']}"}
    fundamentals = extract_fundamentals(facts or {})
    dividend_fact = next((row for row in fundamentals if row["name"] == "dividend_per_share"), None)
    try:
        from core import event_context
    except ImportError:
        import event_context
    events = event_context.for_ticker(symbol)
    corporate = events["corporate_actions"]
    if dividend_fact is not None:
        corporate["latest_dividend_fact"] = dividend_fact
    return {"ticker": symbol, "generated_at": _now(), "profile": profile,
            "fundamentals": fundamentals, "filings": extract_filings(submissions or {}),
            "macro": macro_events(), "earnings": events["earnings"],
            "corporate_actions": corporate,
            "sources": [{"name": "SEC company tickers", "url": SEC_TICKERS}, {"name": "SEC submissions", "url": SEC_SUBMISSIONS.format(cik=company["cik"]) if company else None},
                        {"name": "SEC company facts", "url": SEC_FACTS.format(cik=company["cik"]) if company else None}],
            "errors": errors, "read_only": True, "execution_capability": False}
