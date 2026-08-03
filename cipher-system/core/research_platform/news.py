from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import canonical_json, stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry


@dataclass(frozen=True)
class NewsDocument:
    source: str
    external_id: str
    title: str
    text: str
    publication_time: datetime
    received_at: datetime
    available_at: datetime
    symbols: Sequence[str]
    url_hash: str | None = None
    raw_object_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        publication = _aware(self.publication_time, "publication_time")
        received = _aware(self.received_at, "received_at")
        available = _aware(self.available_at, "available_at")
        if received < publication:
            raise ValueError("received_at cannot precede publication_time")
        if available < received:
            raise ValueError("available_at cannot precede received_at")
        if not self.text.strip():
            raise ValueError("news text cannot be empty")
        object.__setattr__(self, "publication_time", publication)
        object.__setattr__(self, "received_at", received)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "symbols", tuple(sorted({value.upper() for value in self.symbols if value})))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SentimentScore:
    positive: float
    negative: float
    neutral: float
    model_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        values = (self.positive, self.negative, self.neutral)
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("sentiment probabilities must be in [0, 1]")
        if abs(sum(values) - 1.0) > 1e-5:
            raise ValueError("sentiment probabilities must sum to 1")
        object.__setattr__(self, "metadata", dict(self.metadata))


class SentimentProvider(Protocol):
    @property
    def model_id(self) -> str:
        ...

    def score(self, chunks: Sequence[str]) -> Sequence[SentimentScore]:
        ...


class FinBertSentimentProvider:
    """Optional local FinBERT adapter; no remote inference is performed."""

    def __init__(
        self,
        model_id: str = "ProsusAI/finbert",
        *,
        revision: str | None = None,
        device: int = -1,
    ):
        if importlib.util.find_spec("transformers") is None:
            raise RuntimeError("transformers is not installed; FinBERT remains unavailable")
        from transformers import pipeline  # type: ignore

        self._model_id = f"{model_id}@{revision}" if revision else model_id
        self._pipeline = pipeline(
            "text-classification",
            model=model_id,
            tokenizer=model_id,
            revision=revision,
            device=device,
            top_k=None,
            function_to_apply="softmax",
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    def score(self, chunks: Sequence[str]) -> Sequence[SentimentScore]:
        outputs = self._pipeline(list(chunks), truncation=True, max_length=512)
        scores: list[SentimentScore] = []
        for output in outputs:
            labels = {str(item["label"]).lower(): float(item["score"]) for item in output}
            normalized = {
                "positive": labels.get("positive", 0.0),
                "negative": labels.get("negative", 0.0),
                "neutral": labels.get("neutral", 0.0),
            }
            total = sum(normalized.values())
            if total <= 0:
                raise RuntimeError("FinBERT did not return recognized sentiment labels")
            scores.append(
                SentimentScore(
                    positive=normalized["positive"] / total,
                    negative=normalized["negative"] / total,
                    neutral=normalized["neutral"] / total,
                    model_id=self.model_id,
                )
            )
        return scores


@dataclass(frozen=True)
class NewsEventRecord:
    news_event_id: str
    source: str
    external_id: str
    title: str
    publication_time: datetime
    received_at: datetime
    available_at: datetime
    symbols: tuple[str, ...]
    text_sha256: str
    positive_probability: float
    negative_probability: float
    neutral_probability: float
    sentiment_model_id: str
    chunk_count: int
    high_magnitude: bool
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "news_event_id": self.news_event_id,
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "publication_time": self.publication_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "symbols": list(self.symbols),
            "text_sha256": self.text_sha256,
            "positive_probability": self.positive_probability,
            "negative_probability": self.negative_probability,
            "neutral_probability": self.neutral_probability,
            "sentiment_model_id": self.sentiment_model_id,
            "chunk_count": self.chunk_count,
            "high_magnitude": self.high_magnitude,
            "metadata": dict(self.metadata),
            "raw_text_stored": False,
        }


class NewsFeatureService:
    def __init__(self, registry: ResearchRegistry, artifacts: ArtifactStore):
        self.registry = registry
        self.artifacts = artifacts

    def process(
        self,
        document: NewsDocument,
        provider: SentimentProvider,
        *,
        chunk_words: int = 360,
        overlap_words: int = 60,
        high_magnitude_threshold: float = 0.75,
    ) -> tuple[NewsEventRecord, ArtifactReference]:
        chunks = overlapping_word_chunks(document.text, chunk_words=chunk_words, overlap_words=overlap_words)
        scores = tuple(provider.score(chunks))
        if len(scores) != len(chunks):
            raise RuntimeError("sentiment provider returned a different number of scores than chunks")
        positive, negative, neutral = aggregate_sentiment(scores)
        magnitude = max(positive, negative)
        # Event identity is stable across idempotent re-ingestion. Receipt and
        # availability timestamps remain evidence fields but do not create a
        # second event ID for the same external document.
        payload = {
            "source": document.source,
            "external_id": document.external_id,
            "publication_time": document.publication_time.isoformat(),
            "symbols": document.symbols,
            "text_sha256": document.text_sha256,
            "sentiment_model_id": provider.model_id,
            "chunk_count": len(chunks),
        }
        record = NewsEventRecord(
            news_event_id=stable_id("news", payload),
            source=document.source,
            external_id=document.external_id,
            title=document.title,
            publication_time=document.publication_time,
            received_at=document.received_at,
            available_at=document.available_at,
            symbols=tuple(document.symbols),
            text_sha256=document.text_sha256,
            positive_probability=positive,
            negative_probability=negative,
            neutral_probability=neutral,
            sentiment_model_id=provider.model_id,
            chunk_count=len(chunks),
            high_magnitude=magnitude >= high_magnitude_threshold,
            metadata={
                **dict(document.metadata),
                "url_hash": document.url_hash,
                "raw_object_id": document.raw_object_id,
                "chunk_words": chunk_words,
                "overlap_words": overlap_words,
                "aggregation": "length_weighted_mean",
                "llm_escalation_eligible": magnitude >= high_magnitude_threshold,
                "llm_escalation_executed": False,
            },
        )
        artifact = self.artifacts.put_json(
            record.to_dict(),
            metadata={"kind": "news_event_feature", "news_event_id": record.news_event_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        serialized = canonical_json(record.to_dict())
        with self.registry.connect() as db:
            existing = db.execute(
                "select payload_json from news_events where news_event_id = ?",
                (record.news_event_id,),
            ).fetchone()
            if existing and existing["payload_json"] != serialized:
                raise RuntimeError("news event ID collision")
            db.execute(
                """
                insert or ignore into news_events(
                    news_event_id, source, publication_time, received_at, available_at,
                    symbols_json, sentiment_model_id, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.news_event_id,
                    record.source,
                    record.publication_time.isoformat(),
                    record.received_at.isoformat(),
                    record.available_at.isoformat(),
                    json.dumps(record.symbols),
                    record.sentiment_model_id,
                    serialized,
                ),
            )
        self.registry.audit(
            AuditEvent(
                event_type="NEWS_EVENT_FEATURE_CREATED",
                entity_type="news_event",
                entity_id=record.news_event_id,
                occurred_at=utc_now(),
                payload={
                    "artifact_id": artifact.artifact_id,
                    "source": record.source,
                    "symbols": list(record.symbols),
                    "high_magnitude": record.high_magnitude,
                    "sentiment_model_id": record.sentiment_model_id,
                },
            )
        )
        return record, artifact


def overlapping_word_chunks(text: str, *, chunk_words: int = 360, overlap_words: int = 60) -> tuple[str, ...]:
    if chunk_words < 32:
        raise ValueError("chunk_words must be at least 32")
    if overlap_words < 0 or overlap_words >= chunk_words:
        raise ValueError("overlap_words must be between 0 and chunk_words - 1")
    words = text.split()
    if not words:
        raise ValueError("text contains no words")
    step = chunk_words - overlap_words
    chunks = [" ".join(words[start : start + chunk_words]) for start in range(0, len(words), step)]
    return tuple(chunk for chunk in chunks if chunk)


def aggregate_sentiment(scores: Sequence[SentimentScore]) -> tuple[float, float, float]:
    if not scores:
        raise ValueError("scores cannot be empty")
    weights = [float(score.metadata.get("weight", 1.0)) for score in scores]
    if any(weight <= 0 for weight in weights):
        raise ValueError("sentiment weights must be positive")
    total = sum(weights)
    positive = sum(score.positive * weight for score, weight in zip(scores, weights)) / total
    negative = sum(score.negative * weight for score, weight in zip(scores, weights)) / total
    neutral = sum(score.neutral * weight for score, weight in zip(scores, weights)) / total
    normalization = positive + negative + neutral
    return (
        positive / normalization,
        negative / normalization,
        neutral / normalization,
    )


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
