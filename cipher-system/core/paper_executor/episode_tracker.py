from __future__ import annotations

import json
from datetime import timedelta

from .database import PaperExecutorDatabase
from .models import SignalCard, sha256_id


class EpisodeTracker:
    def __init__(self, db: PaperExecutorDatabase, cooldown_minutes: int):
        self.db = db
        self.cooldown = timedelta(minutes=cooldown_minutes)

    def record(self, card: SignalCard, card_id: str) -> tuple[str, bool]:
        with self.db.connect() as db:
            row = db.execute(
                """
                select * from signal_episodes
                where episode_key = ? and ended_at is null
                order by last_seen_at desc limit 1
                """,
                (card.episode_key,),
            ).fetchone()
            if row:
                last_seen = card.captured_at.fromisoformat(row["last_seen_at"])
                if card.captured_at - last_seen <= self.cooldown:
                    episode_id = row["id"]
                    latest = {
                        "ticker": card.ticker,
                        "spot": card.spot,
                        "target": card.target,
                        "invalidation": card.invalidation,
                        "score": card.score,
                        "rank": card.rank,
                    }
                    db.execute(
                        "update signal_episodes set last_seen_at = ?, poll_count = poll_count + 1, latest_json = ? where id = ?",
                        (card.captured_at.isoformat(), json.dumps(latest, default=str), episode_id),
                    )
                    self._insert_update(db, episode_id, card_id, card)
                    return episode_id, True
            episode_id = sha256_id("episode", {"key": card.episode_key, "started": card.captured_at.isoformat()})
            db.execute(
                """
                insert into signal_episodes(id, episode_key, scanner_type, ticker, direction, setup, started_at, last_seen_at, latest_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id, card.episode_key, card.scanner_type, card.ticker, card.direction.value, card.setup,
                    card.captured_at.isoformat(), card.captured_at.isoformat(), json.dumps(card.raw, default=str),
                ),
            )
            self._insert_update(db, episode_id, card_id, card)
            return episode_id, False

    @staticmethod
    def _insert_update(db, episode_id: str, card_id: str, card: SignalCard) -> None:
        db.execute(
            """
            insert or ignore into episode_updates(id, episode_id, card_id, seen_at, spot, target, invalidation, payload_json)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sha256_id("episode_update", {"episode_id": episode_id, "card_id": card_id}),
                episode_id, card_id, card.captured_at.isoformat(), card.spot, card.target,
                card.invalidation, json.dumps(card.raw, default=str),
            ),
        )
