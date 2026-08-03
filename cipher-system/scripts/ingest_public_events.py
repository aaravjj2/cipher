#!/usr/bin/env python3
"""Ingest real public SEC events and triage them with revision-pinned FinBERT.

GDELT is attempted once per run and fails closed on provider throttling. NewsAPI
and Claude extraction require explicit credentials/endpoints and are recorded as
skipped when unavailable. This layer is explanatory/risk context only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.news import FinBertSentimentProvider, NewsDocument, NewsFeatureService
from core.research_platform.registry import ResearchRegistry

SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
PINNED_CIKS = {
    "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "MSFT": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."},
    "NVDA": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA Corp."},
    "GE": {"cik_str": 40545, "ticker": "GE", "title": "GE Aerospace"},
}
FINBERT_MODEL_ID = "ProsusAI/finbert"
FINBERT_REVISION = "4556d13015211d73dccd3fdd39d39232506f3e43"
USER_AGENT = "CipherResearch/1.0 https://github.com/aaravjj2/cipher read-only"
RAW_ROOT = ROOT / "data" / "raw" / "public_events"
GOV = ROOT / "data" / "governance"
OUT = ROOT / "data" / "events"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_raw(source: str, name: str, payload: bytes) -> dict[str, Any]:
    digest = sha256_bytes(payload)
    path = RAW_ROOT / source / f"{name}_{digest[:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if sha256_bytes(path.read_bytes()) != digest:
            raise RuntimeError(f"immutable raw checksum mismatch: {path}")
    else:
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    return {"path": str(path), "sha256": digest, "bytes": len(payload)}


def parse_sec_time(value: str, fallback_date: str) -> datetime:
    candidate = (value or "").strip()
    if candidate:
        try:
            if candidate.endswith("Z"):
                candidate = candidate[:-1] + "+00:00"
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromisoformat(f"{fallback_date}T00:00:00+00:00")


def sec_ticker_map(session: requests.Session) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        response = session.get("https://www.sec.gov/files/company_tickers.json", timeout=45)
        raw = atomic_raw("sec", "company_tickers", response.content)
        response.raise_for_status()
        payload = response.json()
        mapping = {str(item["ticker"]).upper(): item for item in payload.values()}
        return mapping, {**raw, "status": "downloaded"}
    except Exception as exc:
        return dict(PINNED_CIKS), {
            "status": "fallback_pinned_cik_map",
            "reason": f"{type(exc).__name__}: {exc}",
            "symbols": sorted(PINNED_CIKS),
            "mapping_sha256": hashlib.sha256(json.dumps(PINNED_CIKS, sort_keys=True).encode("utf-8")).hexdigest(),
        }


def fetch_sec_documents(
    session: requests.Session,
    *,
    symbols: tuple[str, ...],
    since: datetime,
    max_per_symbol: int,
) -> tuple[list[NewsDocument], list[dict[str, Any]], dict[str, Any]]:
    mapping, ticker_raw = sec_ticker_map(session)
    now = datetime.now(timezone.utc)
    documents: list[NewsDocument] = []
    source_records: list[dict[str, Any]] = []
    for symbol in symbols:
        item = mapping.get(symbol)
        if not item:
            source_records.append({"symbol": symbol, "status": "skipped_no_sec_issuer_mapping"})
            continue
        cik = f"{int(item['cik_str']):010d}"
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            raw = atomic_raw("sec", f"CIK{cik}", response.content)
            payload = response.json()
        except Exception as exc:
            source_records.append({"symbol": symbol, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        recent = payload.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        selected = 0
        for index, accession in enumerate(accessions):
            filing_date = str(recent.get("filingDate", [""] * len(accessions))[index])
            accepted = str(recent.get("acceptanceDateTime", [""] * len(accessions))[index])
            published = parse_sec_time(accepted, filing_date)
            if published < since:
                continue
            form = str(recent.get("form", [""] * len(accessions))[index])
            primary = str(recent.get("primaryDocument", [""] * len(accessions))[index])
            description = str(recent.get("primaryDocDescription", [""] * len(accessions))[index])
            report_date = str(recent.get("reportDate", [""] * len(accessions))[index])
            company = str(payload.get("name") or item.get("title") or symbol)
            title = f"{company} filed {form}"
            text = " — ".join(part for part in (title, description, primary, f"report date {report_date}" if report_date else "") if part)
            accession_compact = str(accession).replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_compact}/{primary}" if primary else url
            documents.append(
                NewsDocument(
                    source="sec_edgar",
                    external_id=str(accession),
                    title=title,
                    text=text,
                    publication_time=published,
                    received_at=published,
                    available_at=published,
                    symbols=(symbol,),
                    url_hash=hashlib.sha256(filing_url.encode("utf-8")).hexdigest(),
                    raw_object_id=raw["sha256"],
                    metadata={
                        "form": form,
                        "filing_date": filing_date,
                        "report_date": report_date,
                        "company": company,
                        "cik": cik,
                        "source_scope": "issuer_filing_metadata_not_article_body",
                        "directional_signal_allowed": False,
                    },
                )
            )
            selected += 1
            if selected >= max_per_symbol:
                break
        source_records.append({"symbol": symbol, "status": "complete", "documents": selected, "raw": raw})
        time.sleep(0.11)
    return documents, source_records, ticker_raw


def fetch_yahoo_documents(
    session: requests.Session,
    *,
    symbols: tuple[str, ...],
    max_per_symbol: int,
) -> tuple[list[NewsDocument], list[dict[str, Any]]]:
    now = datetime.now(timezone.utc)
    documents: list[NewsDocument] = []
    statuses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for symbol in symbols:
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        try:
            response = session.get(
                url,
                params={"q": symbol, "quotesCount": 1, "newsCount": max_per_symbol},
                headers={"User-Agent": "Mozilla/5.0 CipherResearch/1.0"},
                timeout=45,
            )
            raw = atomic_raw("yahoo_finance_search", symbol, response.content)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            statuses.append({"symbol": symbol, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        added = 0
        for item in payload.get("news", []):
            external_id = str(item.get("uuid") or hashlib.sha256(str(item).encode("utf-8")).hexdigest())
            if external_id in seen:
                continue
            seen.add(external_id)
            timestamp = int(item.get("providerPublishTime") or 0)
            if timestamp <= 0:
                continue
            published = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            related = tuple(
                sorted(
                    {
                        str(value).upper()
                        for value in item.get("relatedTickers", [])
                        if str(value).upper() in SYMBOLS
                    }
                    | {symbol}
                )
            )
            publisher = str(item.get("publisher") or "Yahoo Finance search")
            link = str(item.get("link") or "")
            documents.append(
                NewsDocument(
                    source="yahoo_finance_search",
                    external_id=external_id,
                    title=title,
                    text=f"{title} — publisher: {publisher}",
                    publication_time=published,
                    received_at=published,
                    available_at=published,
                    symbols=related,
                    url_hash=hashlib.sha256(link.encode("utf-8")).hexdigest() if link else None,
                    raw_object_id=raw["sha256"],
                    metadata={
                        "publisher": publisher,
                        "story_type": item.get("type"),
                        "source_scope": "headline_metadata_only_not_article_body",
                        "directional_signal_allowed": False,
                    },
                )
            )
            added += 1
        statuses.append({"symbol": symbol, "status": "complete", "documents": added, "raw": raw})
        time.sleep(0.15)
    return documents, statuses


def attempt_gdelt(session: requests.Session, *, since_days: int, max_records: int) -> dict[str, Any]:
    query = " OR ".join(f'"{symbol}"' for symbol in ("AAPL", "MSFT", "NVDA", "GE"))
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote_plus(query)}&mode=artlist&maxrecords={max_records}"
        f"&format=json&sort=datedesc&timespan={since_days}days"
    )
    try:
        response = session.get(url, timeout=45)
        raw = atomic_raw("gdelt", "doc_api", response.content)
        if response.status_code == 429:
            return {"status": "skipped_provider_rate_limited", "http_status": 429, "raw": raw, "documents": 0}
        response.raise_for_status()
        payload = response.json()
        return {
            "status": "fetched_unprocessed",
            "http_status": response.status_code,
            "raw": raw,
            "documents": len(payload.get("articles", [])),
            "reason": "GDELT article bodies are not included; SEC is the governed real-event source in this run.",
        }
    except Exception as exc:
        return {"status": "skipped_unavailable", "documents": 0, "error": f"{type(exc).__name__}: {exc}"}


def existing_news_records(registry: ResearchRegistry) -> dict[tuple[str, str], dict[str, Any]]:
    """Return already-governed events keyed by provider identity."""

    records: dict[tuple[str, str], dict[str, Any]] = {}
    with registry.connect() as db:
        rows = db.execute("select payload_json from news_events").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        source = str(payload.get("source") or "")
        external_id = str(payload.get("external_id") or "")
        if source and external_id:
            records[(source, external_id)] = payload
    return records


def partition_documents(
    documents: list[NewsDocument],
    existing: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[NewsDocument]]:
    """Separate governed provider identities from genuinely new documents."""

    reused: list[dict[str, Any]] = []
    new_documents: list[NewsDocument] = []
    for document in documents:
        prior = existing.get((document.source, document.external_id))
        if prior is not None:
            reused.append({"record": prior, "artifact": None, "ingestion_action": "reused_existing_event"})
        else:
            new_documents.append(document)
    return reused, new_documents


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--max-per-symbol", type=int, default=6)
    parser.add_argument("--skip-finbert", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, args.days))
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    sec_documents, sec_status, ticker_raw = fetch_sec_documents(
        session,
        symbols=SYMBOLS,
        since=since,
        max_per_symbol=max(1, args.max_per_symbol),
    )
    yahoo_documents, yahoo_status = fetch_yahoo_documents(
        session,
        symbols=SYMBOLS,
        max_per_symbol=max(1, args.max_per_symbol),
    )
    documents = [*sec_documents, *yahoo_documents]
    gdelt = attempt_gdelt(session, since_days=min(max(1, args.days), 30), max_records=20)

    registry = ResearchRegistry(GOV / "research_registry.sqlite")
    artifacts = ArtifactStore(ROOT / "data" / "artifacts" / "public_events")
    existing = existing_news_records(registry)
    processed, new_documents = partition_documents(documents, existing)
    model_status: dict[str, Any]
    if args.skip_finbert:
        model_status = {"status": "skipped_by_operator", "model_id": FINBERT_MODEL_ID, "revision": FINBERT_REVISION, "documents_reused": len(processed)}
    elif not documents:
        model_status = {"status": "skipped_no_real_documents", "model_id": FINBERT_MODEL_ID, "revision": FINBERT_REVISION, "documents_reused": 0}
    elif not new_documents:
        model_status = {"status": "complete_no_new_documents", "model_id": FINBERT_MODEL_ID, "revision": FINBERT_REVISION, "documents_scored": 0, "documents_reused": len(processed)}
    else:
        provider = FinBertSentimentProvider(FINBERT_MODEL_ID, revision=FINBERT_REVISION, device=-1)
        service = NewsFeatureService(registry, artifacts)
        for document in new_documents:
            record, artifact = service.process(document, provider, chunk_words=128, overlap_words=16)
            processed.append({"record": record.to_dict(), "artifact": artifact.to_dict(), "ingestion_action": "created"})
        model_status = {
            "status": "complete",
            "model_id": FINBERT_MODEL_ID,
            "revision": FINBERT_REVISION,
            "documents_scored": len(new_documents),
            "documents_reused": len(processed) - len(new_documents),
        }

    newsapi_status = "skipped_no_configured_api_key" if not os.environ.get("NEWSAPI_KEY") else "not_invoked_in_sec_first_run"
    claude_status = "skipped_no_configured_endpoint" if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")) else "not_invoked_without_separate_authorization"
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "period": {"since": since.isoformat(), "through": now.isoformat()},
        "symbols": list(SYMBOLS),
        "sources": {
            "sec_edgar": {"status": "complete" if sec_documents else "unavailable_or_no_recent_documents", "documents": len(sec_documents), "ticker_map_raw": ticker_raw, "symbols": sec_status},
            "yahoo_finance_search": {"status": "complete" if yahoo_documents else "unavailable", "documents": len(yahoo_documents), "symbols": yahoo_status},
            "gdelt": gdelt,
            "newsapi": {"status": newsapi_status},
        },
        "finbert": model_status,
        "structured_event_extraction": {"provider": "claude", "status": claude_status, "executed": False},
        "processed_events": processed,
        "role": "explanatory_and_risk_flagging_only",
        "directional_signal_allowed": False,
        "market_data_used": False,
        "trading_or_execution": False,
        "live_execution": False,
    }
    output = OUT / f"public_event_ingestion_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest = OUT / "latest_public_event_ingestion.json"
    latest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "path": str(output),
        "sec_documents": len(sec_documents),
        "yahoo_documents": len(yahoo_documents),
        "finbert_scored": model_status.get("documents_scored", 0),
        "events_reused": model_status.get("documents_reused", 0),
        "gdelt_status": gdelt.get("status"),
        "newsapi_status": newsapi_status,
        "claude_status": claude_status,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
