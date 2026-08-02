"""Strict option-quote dataset manifests, loading, and eligibility checks.

A file containing option symbols and daily closes is not automatically a
point-in-time option-chain dataset. This module requires provenance metadata
and refuses to silently promote synthetic/fabricated bid-ask values into
executable quotes.

The loader is deliberately standard-library only and produces
``OptionQuote`` objects for ``option_backtest_engine.py``.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable, Mapping

try:
    from .option_backtest_engine import OptionContract, OptionQuote, audit_quote_history
except ImportError:  # Direct module import in tests/scripts.
    from option_backtest_engine import OptionContract, OptionQuote, audit_quote_history


class DatasetValidationError(ValueError):
    """Raised when dataset structure or provenance is invalid."""


class DatasetEligibilityError(DatasetValidationError):
    """Raised when strict research-grade loading is requested for blocked data."""


_ALLOWED_GRANULARITIES = {"tick", "second", "minute", "hour", "daily"}
_ALLOWED_BID_ASK_SOURCES = {"observed", "derived", "fabricated", "absent"}
_BOOLEAN_MANIFEST_FIELDS = (
    "point_in_time",
    "includes_underlying_marks",
    "includes_historical_open_interest",
    "includes_historical_volume",
    "includes_iv_or_greeks",
    "includes_rates",
    "includes_dividends",
    "includes_contract_adjustments",
    "survivorship_safe_universe",
)


def _manifest_bool(payload: Mapping[str, object], field_name: str) -> bool:
    value = payload.get(field_name, False)
    if not isinstance(value, bool):
        raise DatasetValidationError(
            f"manifest field {field_name!r} must be a JSON boolean"
        )
    return value


def _manifest_string_sequence(
    payload: Mapping[str, object],
    field_name: str,
) -> tuple[str, ...]:
    value = payload.get(field_name, ())
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise DatasetValidationError(
            f"manifest field {field_name!r} must be an array of strings"
        )
    if any(not isinstance(item, str) for item in value):
        raise DatasetValidationError(
            f"manifest field {field_name!r} must contain only strings"
        )
    return tuple(value)


@dataclass(frozen=True, slots=True)
class OptionDatasetManifest:
    dataset_id: str
    provider: str
    generated_at: datetime
    quote_granularity: str
    point_in_time: bool
    bid_ask_source: str
    timezone_name: str = "UTC"
    includes_underlying_marks: bool = False
    includes_historical_open_interest: bool = False
    includes_historical_volume: bool = False
    includes_iv_or_greeks: bool = False
    includes_rates: bool = False
    includes_dividends: bool = False
    includes_contract_adjustments: bool = False
    survivorship_safe_universe: bool = False
    source_files: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        generated_at = self.generated_at
        if not isinstance(generated_at, datetime):
            raise ValueError("generated_at must be a datetime")
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        dataset_id = str(self.dataset_id).strip()
        provider = str(self.provider).strip()
        granularity = str(self.quote_granularity).lower().strip()
        bid_ask_source = str(self.bid_ask_source).lower().strip()
        timezone_name = str(self.timezone_name).strip()
        if not dataset_id:
            raise ValueError("dataset_id is required")
        if not provider:
            raise ValueError("provider is required")
        if not timezone_name:
            raise ValueError("timezone_name is required")
        if granularity not in _ALLOWED_GRANULARITIES:
            raise ValueError(
                f"quote_granularity must be one of {sorted(_ALLOWED_GRANULARITIES)}"
            )
        if bid_ask_source not in _ALLOWED_BID_ASK_SOURCES:
            raise ValueError(
                f"bid_ask_source must be one of {sorted(_ALLOWED_BID_ASK_SOURCES)}"
            )
        for field_name in _BOOLEAN_MANIFEST_FIELDS:
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")
        if isinstance(self.source_files, (str, bytes)):
            raise ValueError("source_files must be a sequence of strings")
        if isinstance(self.notes, (str, bytes)):
            raise ValueError("notes must be a sequence of strings")
        if any(not isinstance(item, str) for item in self.source_files):
            raise ValueError("source_files must contain only strings")
        if any(not isinstance(item, str) for item in self.notes):
            raise ValueError("notes must contain only strings")
        object.__setattr__(self, "dataset_id", dataset_id)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "generated_at", generated_at.astimezone(timezone.utc))
        object.__setattr__(self, "quote_granularity", granularity)
        object.__setattr__(self, "bid_ask_source", bid_ask_source)
        object.__setattr__(self, "timezone_name", timezone_name)
        object.__setattr__(self, "source_files", tuple(self.source_files))
        object.__setattr__(self, "notes", tuple(self.notes))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "OptionDatasetManifest":
        generated = payload.get("generated_at")
        if isinstance(generated, datetime):
            generated_at = generated
        elif isinstance(generated, str):
            generated_at = _parse_timestamp(generated, allow_date_only=False)
        else:
            raise DatasetValidationError("manifest generated_at is required")

        return cls(
            dataset_id=str(payload.get("dataset_id") or ""),
            provider=str(payload.get("provider") or ""),
            generated_at=generated_at,
            quote_granularity=str(payload.get("quote_granularity") or ""),
            point_in_time=_manifest_bool(payload, "point_in_time"),
            bid_ask_source=str(payload.get("bid_ask_source") or "absent"),
            timezone_name=str(payload.get("timezone_name") or "UTC"),
            includes_underlying_marks=_manifest_bool(
                payload, "includes_underlying_marks"
            ),
            includes_historical_open_interest=_manifest_bool(
                payload, "includes_historical_open_interest"
            ),
            includes_historical_volume=_manifest_bool(
                payload, "includes_historical_volume"
            ),
            includes_iv_or_greeks=_manifest_bool(payload, "includes_iv_or_greeks"),
            includes_rates=_manifest_bool(payload, "includes_rates"),
            includes_dividends=_manifest_bool(payload, "includes_dividends"),
            includes_contract_adjustments=_manifest_bool(
                payload, "includes_contract_adjustments"
            ),
            survivorship_safe_universe=_manifest_bool(
                payload, "survivorship_safe_universe"
            ),
            source_files=_manifest_string_sequence(payload, "source_files"),
            notes=_manifest_string_sequence(payload, "notes"),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "OptionDatasetManifest":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DatasetValidationError("manifest JSON must contain an object")
        return cls.from_dict(payload)

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "provider": self.provider,
            "generated_at": self.generated_at.isoformat(),
            "quote_granularity": self.quote_granularity,
            "point_in_time": self.point_in_time,
            "bid_ask_source": self.bid_ask_source,
            "timezone_name": self.timezone_name,
            "includes_underlying_marks": self.includes_underlying_marks,
            "includes_historical_open_interest": self.includes_historical_open_interest,
            "includes_historical_volume": self.includes_historical_volume,
            "includes_iv_or_greeks": self.includes_iv_or_greeks,
            "includes_rates": self.includes_rates,
            "includes_dividends": self.includes_dividends,
            "includes_contract_adjustments": self.includes_contract_adjustments,
            "survivorship_safe_universe": self.survivorship_safe_universe,
            "source_files": list(self.source_files),
            "notes": list(self.notes),
        }

    def eligibility(self) -> dict:
        """Return hard blockers and warnings for research-grade option P&L."""
        blockers: list[str] = []
        warnings: list[str] = []

        if not self.point_in_time:
            blockers.append("quotes are not certified point-in-time observations")
        if self.bid_ask_source != "observed":
            blockers.append(
                f"bid/ask source is {self.bid_ask_source!r}, not observed market quotes"
            )
        if self.quote_granularity == "daily":
            warnings.append(
                "daily quote granularity cannot resolve intraday stop/target ordering"
            )
        if not self.includes_underlying_marks:
            blockers.append("timestamp-aligned underlying marks are absent")
        if not self.includes_contract_adjustments:
            blockers.append("OCC contract-adjustment history is absent")
        if not self.includes_rates:
            warnings.append("historical rates are absent")
        if not self.includes_dividends:
            warnings.append("historical dividends/ex-dividend dates are absent")
        if not self.includes_historical_open_interest:
            warnings.append("historical open interest is absent")
        if not self.includes_iv_or_greeks:
            warnings.append("historical IV/Greeks are absent or must be recomputed")
        if not self.survivorship_safe_universe:
            warnings.append("universe survivorship safety is not certified")

        return {
            "eligible_for_research_grade_option_pnl": not blockers,
            "blockers": blockers,
            "warnings": warnings,
        }


@dataclass(frozen=True, slots=True)
class LoadedOptionDataset:
    manifest: OptionDatasetManifest
    quotes: tuple[OptionQuote, ...]
    structural_audit: Mapping[str, object]
    load_warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def eligibility(self) -> dict:
        manifest_status = self.manifest.eligibility()
        blockers = list(manifest_status["blockers"])
        warnings = list(manifest_status["warnings"])
        blockers.extend(str(x) for x in self.structural_audit.get("errors", []))
        warnings.extend(str(x) for x in self.structural_audit.get("warnings", []))
        warnings.extend(self.load_warnings)
        return {
            "eligible_for_research_grade_option_pnl": not blockers,
            "blockers": blockers,
            "warnings": warnings,
        }


_COLUMN_ALIASES = {
    "symbol": ("symbol", "occ_symbol", "option_symbol"),
    "underlying": ("underlying", "underlying_symbol", "root_symbol"),
    "option_type": ("option_type", "type", "right"),
    "strike": ("strike", "strike_price"),
    "expiration": ("expiration", "expiry", "expiration_date"),
    "timestamp": ("timestamp", "quote_timestamp", "quote_datetime", "quote_date"),
    "bid": ("bid", "bid_price"),
    "ask": ("ask", "ask_price"),
    "last": ("last", "last_price", "close"),
    "volume": ("volume",),
    "open_interest": ("open_interest", "oi"),
    "implied_volatility": ("implied_volatility", "iv", "mid_iv"),
    "delta": ("delta",),
    "gamma": ("gamma",),
    "theta": ("theta",),
    "vega": ("vega",),
    "multiplier": ("multiplier", "contract_multiplier"),
    "exercise_style": ("exercise_style", "style"),
    "settlement": ("settlement", "settlement_type"),
}


def _resolve_columns(fieldnames: Iterable[str] | None) -> dict[str, str]:
    available = {name.strip(): name for name in (fieldnames or ()) if name}
    resolved: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in available:
                resolved[canonical] = available[alias]
                break

    required = {
        "symbol",
        "underlying",
        "option_type",
        "strike",
        "expiration",
        "timestamp",
        "bid",
        "ask",
    }
    missing = sorted(required - resolved.keys())
    if missing:
        raise DatasetValidationError(
            f"missing required option quote columns: {', '.join(missing)}"
        )
    return resolved


def _parse_timestamp(value: str, *, allow_date_only: bool) -> datetime:
    raw = value.strip()
    if not raw:
        raise DatasetValidationError("empty quote timestamp")

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DatasetValidationError(f"invalid timestamp {value!r}") from exc

    is_date_only = "T" not in raw and " " not in raw
    if is_date_only:
        if not allow_date_only:
            raise DatasetValidationError(
                f"date-only timestamp {value!r} is not an executable quote time"
            )
        parsed = datetime.combine(parsed.date(), time(0, 0), tzinfo=timezone.utc)

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DatasetValidationError(
            f"timestamp {value!r} lacks an explicit timezone offset"
        )
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise DatasetValidationError(f"invalid {field_name}: {value!r}") from exc


def _parse_float(
    row: Mapping[str, str],
    column: str | None,
    *,
    required: bool = False,
) -> float | None:
    if column is None:
        return None
    raw = (row.get(column) or "").strip()
    if not raw:
        if required:
            raise DatasetValidationError(f"missing numeric value in column {column}")
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise DatasetValidationError(
            f"invalid numeric value {raw!r} in column {column}"
        ) from exc
    if not math.isfinite(value):
        raise DatasetValidationError(
            f"non-finite numeric value {raw!r} in column {column}"
        )
    return value


def _parse_int(
    row: Mapping[str, str],
    column: str | None,
    *,
    default: int | None = None,
) -> int | None:
    value = _parse_float(row, column)
    if value is None:
        return default
    if int(value) != value:
        raise DatasetValidationError(
            f"expected integer value in column {column}, got {value}"
        )
    return int(value)


def load_option_quotes_csv(
    path: str | Path,
    manifest: OptionDatasetManifest,
    *,
    strict_research_grade: bool = True,
    allow_date_only_timestamps: bool = False,
) -> LoadedOptionDataset:
    """Load observed option quotes from CSV with manifest-gated provenance.

    ``strict_research_grade=True`` checks manifest blockers before parsing and
    checks combined manifest/structural blockers after parsing. The loader never
    derives bid/ask from close/last.
    """
    manifest_status = manifest.eligibility()
    if strict_research_grade and manifest_status["blockers"]:
        raise DatasetEligibilityError(
            "dataset manifest blocks research-grade loading: "
            + "; ".join(manifest_status["blockers"])
        )

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)

    quotes: list[OptionQuote] = []
    load_warnings: list[str] = []
    date_only_timestamp_rows = 0
    contract_by_symbol: dict[str, OptionContract] = {}

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _resolve_columns(reader.fieldnames)
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp_raw = row.get(columns["timestamp"], "")
                timestamp = _parse_timestamp(
                    timestamp_raw,
                    allow_date_only=allow_date_only_timestamps,
                )
                if "T" not in timestamp_raw and " " not in timestamp_raw:
                    date_only_timestamp_rows += 1

                expiration = _parse_date(
                    row.get(columns["expiration"], ""),
                    field_name="expiration",
                )
                if timestamp.date() > expiration:
                    raise DatasetValidationError(
                        f"quote timestamp is after expiration {expiration}"
                    )

                symbol = (row.get(columns["symbol"]) or "").strip()
                underlying = (row.get(columns["underlying"]) or "").strip()
                option_type = (row.get(columns["option_type"]) or "").strip().lower()
                if option_type in {"c", "call"}:
                    option_type = "call"
                elif option_type in {"p", "put"}:
                    option_type = "put"

                contract = OptionContract(
                    symbol=symbol,
                    underlying=underlying,
                    option_type=option_type,
                    strike=float(_parse_float(row, columns["strike"], required=True)),
                    expiration=expiration,
                    multiplier=_parse_int(row, columns.get("multiplier"), default=100) or 100,
                    exercise_style=(
                        (row.get(columns.get("exercise_style", "")) or "american")
                        .strip()
                        .lower()
                    ),
                    settlement=(
                        (row.get(columns.get("settlement", "")) or "physical")
                        .strip()
                        .lower()
                    ),
                )
                existing = contract_by_symbol.get(symbol)
                if existing is not None and existing != contract:
                    raise DatasetValidationError(
                        f"contract metadata changed for symbol {symbol}"
                    )
                contract_by_symbol[symbol] = contract

                quotes.append(
                    OptionQuote(
                        contract=contract,
                        timestamp=timestamp,
                        bid=float(_parse_float(row, columns["bid"], required=True)),
                        ask=float(_parse_float(row, columns["ask"], required=True)),
                        last=_parse_float(row, columns.get("last")),
                        volume=_parse_int(row, columns.get("volume")),
                        open_interest=_parse_int(row, columns.get("open_interest")),
                        implied_volatility=_parse_float(
                            row, columns.get("implied_volatility")
                        ),
                        delta=_parse_float(row, columns.get("delta")),
                        gamma=_parse_float(row, columns.get("gamma")),
                        theta=_parse_float(row, columns.get("theta")),
                        vega=_parse_float(row, columns.get("vega")),
                    )
                )
            except (DatasetValidationError, ValueError) as exc:
                raise DatasetValidationError(
                    f"{source}:{line_number}: {exc}"
                ) from exc

    if date_only_timestamp_rows:
        load_warnings.append(
            "date-only quote timestamps normalized to midnight UTC: "
            f"{date_only_timestamp_rows} rows"
        )

    structural_audit = audit_quote_history(quotes)
    loaded = LoadedOptionDataset(
        manifest=manifest,
        quotes=tuple(quotes),
        structural_audit=structural_audit,
        load_warnings=tuple(load_warnings),
    )
    combined = loaded.eligibility
    if strict_research_grade and combined["blockers"]:
        raise DatasetEligibilityError(
            "loaded dataset is not research-grade eligible: "
            + "; ".join(combined["blockers"])
        )
    return loaded


def conservative_manifest_for_unverified_file(
    path: str | Path,
    *,
    provider: str = "unknown",
) -> OptionDatasetManifest:
    """Create an explicitly blocked manifest for legacy/unverified files."""
    source = Path(path)
    return OptionDatasetManifest(
        dataset_id=f"unverified:{source.name}",
        provider=provider,
        generated_at=datetime.now(timezone.utc),
        quote_granularity="daily",
        point_in_time=False,
        bid_ask_source="absent",
        source_files=(str(source),),
        notes=(
            "Auto-generated conservative manifest; provenance must be supplied before use.",
        ),
    )
