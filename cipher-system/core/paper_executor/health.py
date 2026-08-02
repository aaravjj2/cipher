from __future__ import annotations

from .config import ExecutorConfig
from .database import PaperExecutorDatabase
from .models import Mode


def health_payload(cfg: ExecutorConfig, db: PaperExecutorDatabase, mode: Mode, feed_degraded: bool) -> dict:
    return {
        "ok": db.integrity_ok(),
        "mode": mode.value,
        "bind": f"{cfg.server.host}:{cfg.server.port}",
        "database": str(cfg.database_path),
        "market_data": {"provider": cfg.market_data.provider, "degraded": feed_degraded},
        "kill_switch": cfg.kill_switch_path.exists(),
        "paper_only": True,
    }
