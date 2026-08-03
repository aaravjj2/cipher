from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_script(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / f"{name}.py"
    module_name = f"cipher_test_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def create_registry(path: Path, *, news_id: str | None = None, contaminated: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            create table news_events (
                news_event_id text primary key,
                source text not null,
                publication_time text not null,
                received_at text not null,
                available_at text not null,
                symbols_json text not null,
                sentiment_model_id text,
                payload_json text not null
            );
            create table raw_objects (
                raw_object_id text primary key,
                source text not null,
                dataset text not null,
                uri text not null,
                checksum text not null,
                checksum_method text not null,
                size_bytes integer not null,
                received_at text not null,
                available_at text not null,
                ingestion_run_id text not null,
                disposition text not null,
                payload_json text not null
            );
            create table audit_events (
                event_id text primary key,
                event_type text not null,
                entity_type text not null,
                entity_id text not null,
                occurred_at text not null,
                actor text not null,
                payload_json text not null
            );
            """
        )
        if news_id:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "insert into news_events values (?,?,?,?,?,?,?,?)",
                (news_id, "test", now, now, now, "[]", None, "{}"),
            )
        if contaminated:
            raw_id = "raw_7bd7d51f57d93436f54a1375"
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "insert into raw_objects values (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    raw_id,
                    "browser_gcs_capture",
                    "scanner_flash_raw",
                    "file:///tmp/pytest-of-user/test.json",
                    "x" * 64,
                    "sha256",
                    1,
                    now,
                    now,
                    "test-run",
                    "immutable_raw",
                    "{}",
                ),
            )
            db.execute(
                "insert into audit_events values (?,?,?,?,?,?,?)",
                (
                    "audit_test",
                    "RAW_OBJECT_REGISTERED",
                    "raw_object",
                    raw_id,
                    now,
                    "system",
                    '{"uri":"file:///tmp/pytest-of-user/test.json"}',
                ),
            )
        db.commit()


def test_unification_moves_state_merges_registry_and_builds_compatibility_links(tmp_path: Path, monkeypatch):
    module = load_script("unify_cipher_runtime")
    old_root = tmp_path / "legacy" / "cipher-system"
    canonical_root = tmp_path / "git" / "cipher-system"
    runtime_root = tmp_path / "runtime"
    backup_root = runtime_root / "backups" / "test"
    for root in (old_root, canonical_root):
        (root / "app").mkdir(parents=True)
        (root / "data" / "governance").mkdir(parents=True)
        (root / "logs").mkdir(parents=True)
    (old_root / "data" / "legacy.txt").write_text("legacy", encoding="utf-8")
    (canonical_root / "data" / "new.txt").write_text("canonical", encoding="utf-8")
    (old_root / "logs" / "legacy.log").write_text("legacy", encoding="utf-8")
    (canonical_root / "logs" / "new.log").write_text("new", encoding="utf-8")
    (old_root / ".env").write_text("TRADIER_PRODUCTION_TOKEN=legacy\n", encoding="utf-8")
    (old_root / "app" / ".env").write_text("ALPACA_ALGO_KEY=market\n", encoding="utf-8")
    (old_root / "app" / ".scanner-ingest-token").write_text("scanner-token\n", encoding="utf-8")
    create_registry(old_root / "data" / "governance" / "research_registry.sqlite", news_id="legacy_news")
    create_registry(
        canonical_root / "data" / "governance" / "research_registry.sqlite",
        news_id="canonical_news",
        contaminated=True,
    )

    monkeypatch.setattr(module, "active_cipher_processes", lambda: [])
    paths = module.MigrationPaths(old_root, canonical_root, runtime_root, backup_root)
    result = module.execute(paths)

    assert result["execution_authority"] is False
    assert (runtime_root / "data" / "legacy.txt").read_text(encoding="utf-8") == "legacy"
    assert (runtime_root / "data" / "new.txt").read_text(encoding="utf-8") == "canonical"
    assert old_root.joinpath("data").is_symlink()
    assert canonical_root.joinpath("data").is_symlink()
    assert old_root.joinpath("data").resolve() == runtime_root.joinpath("data").resolve()
    assert canonical_root.joinpath("data").resolve() == runtime_root.joinpath("data").resolve()
    assert old_root.joinpath("logs").resolve() == runtime_root.joinpath("logs").resolve()
    assert canonical_root.joinpath("logs").resolve() == runtime_root.joinpath("logs").resolve()
    assert canonical_root.joinpath(".env").resolve() == runtime_root.joinpath("config/cipher.env").resolve()
    assert canonical_root.joinpath("app/.scanner-ingest-token").resolve() == runtime_root.joinpath("config/scanner-ingest-token").resolve()
    environment = (runtime_root / "config" / "cipher.env").read_text(encoding="utf-8")
    assert "TRADIER_PRODUCTION_TOKEN=legacy" in environment
    assert "ALPACA_ALGO_KEY=market" in environment

    with sqlite3.connect(runtime_root / "data" / "governance" / "research_registry.sqlite") as db:
        assert {row[0] for row in db.execute("select news_event_id from news_events")} == {
            "legacy_news",
            "canonical_news",
        }
        assert db.execute("select count(*) from raw_objects").fetchone()[0] == 0
        assert db.execute("select count(*) from audit_events").fetchone()[0] == 0


def test_live_option_chain_health_uses_project_relative_data(tmp_path: Path):
    module = load_script("hermes_data_health_alerts")
    chain_dir = tmp_path / "live_option_chains"
    chain_dir.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    (chain_dir / "latest_SPY.json").write_text(f'{{"as_of":"{now}"}}', encoding="utf-8")
    result = module.latest_live_option_chains(chain_dir, ("SPY", "QQQ"))
    assert result["ok"] is True
    assert "SPY" in result["per_ticker"]
    assert result["missing"] == ["QQQ"]
