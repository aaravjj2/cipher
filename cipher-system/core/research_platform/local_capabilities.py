from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import kronos_research, timesfm_walkforward

from .external_integrations import DEFAULT_EXTERNAL_ROOT, integration_status
from .models import PromotionState


DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _package_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def build_local_capability_report(
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
    *,
    external_root: str | Path = DEFAULT_EXTERNAL_ROOT,
) -> dict[str, Any]:
    """Describe local model and repository readiness without running a model or vendor call."""

    root = Path(repository_root).resolve()
    timesfm_model_dir = root / "cipher-system" / "data" / "timesfm_model"
    timesfm_manifest = timesfm_model_dir / "manifest.json"
    timesfm_status = timesfm_walkforward.runtime_status(
        model_dir=timesfm_model_dir,
        manifest_path=timesfm_manifest,
    )
    external = integration_status(external_root)
    blocked_execution = {
        "live_execution": False,
        "maximum_promotion_state": PromotionState.LIVE_REVIEW_REQUIRED.value,
        "order_authority": False,
    }
    research_packages = {
        "timesfm": _package_available("timesfm"),
        "torch": _package_available("torch"),
        "qlib": _package_available("qlib"),
        "rdagent": _package_available("rdagent"),
        "vectorbt": _package_available("vectorbt"),
        "riskfolio": _package_available("riskfolio"),
        "transformers": _package_available("transformers"),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(root),
        "models": {
            "kronos": kronos_research.status(),
            "timesfm": timesfm_status,
        },
        "research_packages": research_packages,
        "external_integrations": external,
        "execution_boundary": blocked_execution,
        "safe_for_local_research": not external["boundary_violations"],
        "notes": [
            "This report performs no model inference, vendor request, cloud write, or order action.",
            "A model is not promoted merely because its runtime package is installed.",
            "TimesFM requires a verified manifest, weights, and post-training observations before forecasting.",
        ],
    }
