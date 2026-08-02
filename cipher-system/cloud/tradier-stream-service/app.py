"""Cloud Run wrapper for read-only Tradier market-data capture.

This service captures short Tradier streaming windows and uploads raw JSONL to
GCS when GCS_BUCKET is set. It never calls account, order, preview, or trading
endpoints.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


try:
    from google.cloud import storage
except Exception:  # pragma: no cover - local minimal runs can omit the package.
    storage = None


API_BASE = "https://api.tradier.com"
STREAM_BASE = "https://stream.tradier.com"
DEFAULT_SYMBOLS = "SPY,QQQ,IWM,NVDA,MSFT,AAPL,AVGO,AMZN,IBIT,GOOGL,TSLA,META,MU,AMD"
DEFAULT_FILTERS = "quote,trade,timesale,tradex,summary"
MAX_DURATION_SECONDS = int(os.environ.get("MAX_CAPTURE_SECONDS", "3300"))
TMP_DIR = Path(os.environ.get("CAPTURE_TMP_DIR", "/tmp/cipher-tradier-stream"))


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def token() -> str:
    value = os.environ.get("TRADIER_ACCESS_TOKEN") or os.environ.get("TRADIER_TOKEN")
    if not value:
        raise RuntimeError("TRADIER_ACCESS_TOKEN is required.")
    return value


def split_csv(raw: str) -> list[str]:
    out = []
    seen = set()
    for part in raw.split(","):
        item = part.strip().upper()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def create_session(access_token: str) -> str:
    req = urllib.request.Request(
        f"{API_BASE}/v1/markets/events/session",
        data=b"",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    def walk(value: Any) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"sessionid", "session_id"} and item:
                    return str(item)
                found = walk(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return None

    session_id = walk(payload)
    if not session_id:
        raise RuntimeError(f"Tradier did not return a stream session id: {payload}")
    return session_id


def upload_to_gcs(path: Path, object_name: str) -> str | None:
    bucket_name = os.environ.get("GCS_BUCKET")
    if not bucket_name:
        return None
    if storage is None:
        raise RuntimeError("google-cloud-storage is not installed.")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(path), content_type="application/x-ndjson")
    return f"gs://{bucket_name}/{object_name}"


def capture(symbols: list[str], filters: list[str], duration_seconds: int) -> dict[str, Any]:
    duration_seconds = max(1, min(duration_seconds, MAX_DURATION_SECONDS))
    started_at = utcnow()
    access_token = token()
    session_id = create_session(access_token)
    params = {
        "sessionid": session_id,
        "symbols": ",".join(symbols),
        "filter": ",".join(filters),
        "linebreak": "true",
        "validOnly": "true",
    }
    url = f"{STREAM_BASE}/v1/markets/events?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    local_path = TMP_DIR / datetime.now(timezone.utc).strftime("%Y-%m-%d") / f"{run_stamp}.jsonl"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    event_count = 0
    deadline = time.monotonic() + duration_seconds
    socket.setdefaulttimeout(5)
    with urllib.request.urlopen(req, timeout=10) as resp, local_path.open("w", encoding="utf-8") as handle:
        while time.monotonic() < deadline:
            try:
                raw = resp.readline()
            except socket.timeout:
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                line = json.dumps({"type": "parse_error", "raw": line}, separators=(",", ":"))
            handle.write(line + "\n")
            event_count += 1
    object_name = f"tradier-stream/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}/{local_path.name}"
    gcs_uri = upload_to_gcs(local_path, object_name)
    return {
        "started_at": started_at,
        "completed_at": utcnow(),
        "symbols": symbols,
        "filters": filters,
        "duration_seconds": duration_seconds,
        "event_count": event_count,
        "local_path": str(local_path),
        "gcs_uri": gcs_uri,
        "read_only": True,
    }


class Handler(BaseHTTPRequestHandler):
    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name.
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/health":
            self.write_json(200, {"ok": True, "time": utcnow(), "read_only": True})
            return
        if parsed.path != "/capture":
            self.write_json(404, {"error": "Use /health or /capture."})
            return
        try:
            symbols = split_csv(query.get("symbols", [DEFAULT_SYMBOLS])[0])
            filters = split_csv(query.get("filters", [DEFAULT_FILTERS])[0].lower())
            duration = int(query.get("duration", ["300"])[0])
            if not symbols:
                raise ValueError("At least one symbol is required.")
            if not filters:
                raise ValueError("At least one filter is required.")
            self.write_json(200, capture(symbols, filters, duration))
        except Exception as exc:
            self.write_json(500, {"error": str(exc), "read_only": True})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(json.dumps({"time": utcnow(), "client": self.client_address[0], "message": fmt % args}), flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(json.dumps({"event": "listening", "port": port, "time": utcnow()}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
