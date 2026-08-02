from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .database import PaperExecutorDatabase
from .models import sha256_id

RETRY_DELAYS = [60, 120, 300, 900, 1800, 3600]


class VmForwarder:
    def __init__(self, db: PaperExecutorDatabase, queue_dir: Path, endpoint: str | None):
        self.db = db
        self.queue_dir = Path(queue_dir)
        self.endpoint = endpoint
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    def enqueue(self, batch_id: str, payload: dict[str, Any]) -> str:
        item_id = sha256_id("forward", {"batch_id": batch_id, "payload": payload})
        body = json.dumps(payload, default=str)
        tmp = self.queue_dir / f"{item_id}.tmp"
        final = self.queue_dir / f"{item_id}.json"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(final)
        with self.db.connect() as db:
            db.execute(
                "insert or ignore into forward_queue(id, batch_id, status, endpoint, payload_json) values (?, ?, ?, ?, ?)",
                (item_id, batch_id, "PENDING", self.endpoint, body),
            )
        return item_id

    def attempt(self, item_id: str) -> bool:
        if not self.endpoint:
            return False
        with self.db.connect() as db:
            row = db.execute("select * from forward_queue where id = ?", (item_id,)).fetchone()
            if not row:
                return False
            try:
                req = urllib.request.Request(self.endpoint, data=row["payload_json"].encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    ok = 200 <= resp.status < 300
                if ok:
                    db.execute("update forward_queue set status = 'SENT', attempts = attempts + 1, last_error = null where id = ?", (item_id,))
                    return True
            except Exception as exc:
                delay = RETRY_DELAYS[min(int(row["attempts"]), len(RETRY_DELAYS) - 1)]
                next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                db.execute(
                    "update forward_queue set status = 'FAILED_RETRYABLE', attempts = attempts + 1, next_attempt_at = ?, last_error = ? where id = ?",
                    (next_attempt, str(exc), item_id),
                )
        return False
