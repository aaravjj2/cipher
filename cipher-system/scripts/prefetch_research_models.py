#!/usr/bin/env python3
"""Cache and smoke-test archived research checkpoints without market data.

The checkpoints in this module are retained for reproducibility only.  A
successful download or synthetic inference does not change any research verdict,
promotion state, or execution boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
KRONOS_ROOT = WORKSPACE / "Stock data" / "external" / "Kronos"

MODEL_SPECS: tuple[dict[str, str], ...] = (
    {
        "name": "kronos_mini",
        "repo_id": "NeoQuasar/Kronos-mini",
        "revision": "f4e68697d9d5aed55cef5c96aabc3376bcad9f81",
        "research_status": "archived_reproducibility_only",
    },
    {
        "name": "kronos_tokenizer_base",
        "repo_id": "NeoQuasar/Kronos-Tokenizer-base",
        "revision": "0e0117387f39004a9016484a186a908917e22426",
        "research_status": "archived_reproducibility_only",
    },
    {
        "name": "timesfm_2p5_200m_pytorch",
        "repo_id": "google/timesfm-2.5-200m-pytorch",
        "revision": "1d952420fba87f3c6dee4f240de0f1a0fbc790e3",
        "research_status": "rejected_current_formulation_reproducibility_only",
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_evidence(path: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        evidence.append(
            {
                "path": str(item.relative_to(path)),
                "bytes": item.stat().st_size,
                "sha256": sha256(item),
            }
        )
    return evidence


def prefetch_models(*, cache_dir: Path, offline: bool = False) -> list[dict[str, Any]]:
    from huggingface_hub import snapshot_download

    records: list[dict[str, Any]] = []
    for spec in MODEL_SPECS:
        snapshot = Path(
            snapshot_download(
                repo_id=spec["repo_id"],
                revision=spec["revision"],
                cache_dir=str(cache_dir),
                local_files_only=offline,
            )
        ).resolve()
        files = snapshot_evidence(snapshot)
        records.append(
            {
                **spec,
                "snapshot_path": str(snapshot),
                "file_count": len(files),
                "total_bytes": sum(int(item["bytes"]) for item in files),
                "files": files,
                "cached": True,
            }
        )
    return records


def smoke_timesfm() -> dict[str, Any]:
    import numpy as np
    from timesfm import ForecastConfig, TimesFM_2p5_200M_torch

    model = TimesFM_2p5_200M_torch.from_pretrained(
        "google/timesfm-2.5-200m-pytorch",
        revision="1d952420fba87f3c6dee4f240de0f1a0fbc790e3",
    )
    model.compile(ForecastConfig(max_context=64, max_horizon=4))
    context = np.linspace(100.0, 110.0, 64, dtype=np.float32)
    point, quantiles = model.forecast(horizon=4, inputs=[context])
    point_array = np.asarray(point)
    quantile_array = np.asarray(quantiles)
    return {
        "passed": bool(np.isfinite(point_array).all() and np.isfinite(quantile_array).all()),
        "input": "synthetic_linear_sequence_only",
        "point_shape": list(point_array.shape),
        "quantile_shape": list(quantile_array.shape),
        "market_data_used": False,
        "outcomes_evaluated": False,
    }


def smoke_kronos() -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    if str(KRONOS_ROOT) not in sys.path:
        sys.path.insert(0, str(KRONOS_ROOT))
    from model import Kronos, KronosPredictor, KronosTokenizer

    tokenizer = KronosTokenizer.from_pretrained(
        "NeoQuasar/Kronos-Tokenizer-base",
        revision="0e0117387f39004a9016484a186a908917e22426",
    )
    model = Kronos.from_pretrained(
        "NeoQuasar/Kronos-mini",
        revision="f4e68697d9d5aed55cef5c96aabc3376bcad9f81",
    )
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=64)
    timestamps = pd.date_range("2024-01-02 09:30", periods=68, freq="5min")
    base = np.linspace(100.0, 105.0, 64)
    frame = pd.DataFrame(
        {
            "open": base,
            "high": base + 0.4,
            "low": base - 0.4,
            "close": base + 0.1,
        }
    )
    prediction = predictor.predict(
        df=frame,
        x_timestamp=pd.Series(timestamps[:64]),
        y_timestamp=pd.Series(timestamps[64:]),
        pred_len=4,
        T=1.0,
        top_p=0.9,
        sample_count=1,
        verbose=False,
    )
    return {
        "passed": bool(np.isfinite(prediction.to_numpy()).all()),
        "input": "synthetic_ohlc_sequence_only",
        "shape": list(prediction.shape),
        "columns": list(prediction.columns),
        "market_data_used": False,
        "outcomes_evaluated": False,
    }


def build_manifest(
    *,
    cache_dir: Path,
    offline: bool,
    run_smoke: bool,
) -> dict[str, Any]:
    os.environ.setdefault("HF_HOME", str(cache_dir.parent))
    records = prefetch_models(cache_dir=cache_dir, offline=offline)
    smoke: dict[str, Any] = {"requested": run_smoke}
    if run_smoke:
        smoke.update({"timesfm": smoke_timesfm(), "kronos": smoke_kronos()})
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "archived model reproducibility and synthetic runtime verification",
        "models": records,
        "synthetic_smoke": smoke,
        "research_verdicts_changed": False,
        "market_data_used": False,
        "ranking_or_forecast_outcomes_evaluated": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "huggingface" / "hub",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = build_manifest(
        cache_dir=args.cache_dir.expanduser().resolve(),
        offline=bool(args.offline),
        run_smoke=bool(args.smoke),
    )
    output = args.output or (
        ROOT
        / "data"
        / "governance"
        / f"research_model_cache_manifest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0 if all(item.get("cached") for item in payload["models"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
