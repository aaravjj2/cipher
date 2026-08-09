from __future__ import annotations

import json
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

from .models import Mode


ROOT = Path(__file__).resolve().parents[2]

# The runtime root was a hardcoded Windows path, which made this whole subsystem
# unstartable anywhere else — it is why 24 modules and 14 test files sat unused on
# a Linux box and a Linux VM. The env var keeps the Windows deployment working
# unchanged; the default is now somewhere that exists on the machine running it.
DEFAULT_RUNTIME = Path(
    os.environ.get("CIPHER_PAPER_RUNTIME") or (ROOT / "data" / "paper_runtime")
)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    approved_origins: tuple[str, ...] = ()
    control_token_path: Path = DEFAULT_RUNTIME / "state" / "control.token"
    max_body_bytes: int = 262_144
    rate_limit_per_minute: int = 120

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("Paper executor must bind only to 127.0.0.1.")
        if "*" in self.approved_origins:
            raise ValueError("Wildcard CORS origins are forbidden.")


@dataclass(frozen=True)
class ScannerConfig:
    accepted_types: tuple[str, ...] = ("flash_agentic", "flash")
    episode_cooldown_minutes: int = 10
    maximum_signal_age_seconds: int = 120
    maximum_level_distance_pct: float = 20.0


@dataclass(frozen=True)
class StrategyConfig:
    allowed_setups: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        "flash_agentic": (),
        "flash": ("floor bounce",),
    })
    allowed_tickers: tuple[str, ...] = ("NVDA", "GOOGL", "AVGO")
    entry_window_et_start: str | None = None
    entry_window_et_end: str | None = None
    allowed_patterns: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class InstrumentConfig:
    model: str = "long_option"
    maximum_spread_width: float = 10.0
    minimum_spread_width: float = 1.0


@dataclass(frozen=True)
class MarketDataConfig:
    provider: str = "tradier_production"
    credential_service: str = "cipher-paper-executor"
    credential_username: str = "tradier-market-token"
    quote_maximum_age_seconds: int = 2
    reconnect_initial_seconds: int = 1
    reconnect_maximum_seconds: int = 60


@dataclass(frozen=True)
class ContractConfig:
    minimum_dte: int = 1
    maximum_dte: int = 3
    allow_0dte: bool = False
    preferred_moneyness: str = "atm"
    fallback_moneyness: str = "one_strike_itm"
    minimum_bid: float = 0.05
    maximum_spread_pct: float = 12.0
    minimum_open_interest: int = 100
    minimum_volume: int = 10
    maximum_contract_cost: float = 700.0


@dataclass(frozen=True)
class PortfolioConfig:
    starting_cash: float = 5000.0
    quantity_per_trade: int = 1
    maximum_open_positions: int = 3
    maximum_positions_per_ticker: int = 1
    maximum_new_positions_per_day: int = 5
    stop_after_daily_losses: int = 2


@dataclass(frozen=True)
class ExitConfig:
    take_profit_pct: float = 20.0
    stop_loss_pct: float = 15.0
    exit_on_underlying_target: bool = True
    exit_on_underlying_invalidation: bool = True
    maximum_hold_minutes: int = 45
    force_close_time_et: str = "15:45"
    allow_overnight: bool = False


@dataclass(frozen=True)
class SimulationConfig:
    entry_at_ask: bool = True
    exit_at_bid: bool = True
    minimum_slippage_dollars: float = 0.01
    slippage_pct: float = 0.5


@dataclass(frozen=True)
class VmForwardingConfig:
    enabled: bool = True
    asynchronous: bool = True
    endpoint: str | None = None


@dataclass(frozen=True)
class SafetyConfig:
    live_order_code_present: bool = False
    default_start_mode: Mode = Mode.SHADOW
    require_reconciliation_after_restart: bool = True
    close_positions_on_kill: bool = False


@dataclass(frozen=True)
class ExecutorConfig:
    mode: Mode = Mode.SHADOW
    runtime_root: Path = DEFAULT_RUNTIME
    database_path: Path = DEFAULT_RUNTIME / "data" / "paper_trades" / "flash_paper.sqlite"
    server: ServerConfig = field(default_factory=ServerConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    contract: ContractConfig = field(default_factory=ContractConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    vm_forwarding: VmForwardingConfig = field(default_factory=VmForwardingConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    @property
    def kill_switch_path(self) -> Path:
        return self.runtime_root / "STOP_PAPER_EXECUTOR"


_TOP = {
    "mode", "runtime_root", "database_path", "server", "scanner", "strategy",
    "market_data", "instrument", "contract", "portfolio", "exit", "simulation",
    "vm_forwarding", "safety",
}


def _reject_unknown(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown {context} configuration fields: {sorted(unknown)}")


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text) if text.strip().startswith("{") else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be a mapping.")
    return data


def load_config(path: str | Path | None = None) -> ExecutorConfig:
    data = _read_mapping(Path(path)) if path else {}
    _reject_unknown(data, _TOP, "top-level")
    runtime_root = Path(data.get("runtime_root", DEFAULT_RUNTIME))
    database_path = Path(data.get("database_path", runtime_root / "data" / "paper_trades" / "flash_paper.sqlite"))
    mode = Mode(data.get("mode", "shadow"))
    server_data = data.get("server", {})
    scanner_data = data.get("scanner", {})
    strategy_data = data.get("strategy", {})
    market_data = data.get("market_data", {})
    instrument_data = data.get("instrument", {})
    contract_data = data.get("contract", {})
    portfolio_data = data.get("portfolio", {})
    exit_data = data.get("exit", {})
    simulation_data = data.get("simulation", {})
    vm_data = data.get("vm_forwarding", {})
    safety_data = data.get("safety", {})
    for name, section in {
        "server": server_data, "scanner": scanner_data, "strategy": strategy_data,
        "market_data": market_data, "instrument": instrument_data, "contract": contract_data, "portfolio": portfolio_data,
        "exit": exit_data, "simulation": simulation_data, "vm_forwarding": vm_data,
        "safety": safety_data,
    }.items():
        if not isinstance(section, dict):
            raise ValueError(f"{name} configuration must be a mapping.")
    server = ServerConfig(
        host=server_data.get("host", "127.0.0.1"),
        port=int(server_data.get("port", 8787)),
        approved_origins=tuple(server_data.get("approved_origins", ())),
        control_token_path=Path(server_data.get("control_token_path", runtime_root / "state" / "control.token")),
        max_body_bytes=int(server_data.get("max_body_bytes", 262_144)),
        rate_limit_per_minute=int(server_data.get("rate_limit_per_minute", 120)),
    )
    if server.host != "127.0.0.1":
        raise ValueError("Paper executor must bind only to 127.0.0.1.")
    if "*" in server.approved_origins:
        raise ValueError("Wildcard CORS origins are forbidden.")
    safety = SafetyConfig(
        live_order_code_present=bool(safety_data.get("live_order_code_present", False)),
        default_start_mode=Mode(safety_data.get("default_start_mode", "shadow")),
        require_reconciliation_after_restart=bool(safety_data.get("require_reconciliation_after_restart", True)),
        close_positions_on_kill=bool(safety_data.get("close_positions_on_kill", False)),
    )
    if safety.live_order_code_present or safety.default_start_mode != Mode.SHADOW:
        raise ValueError("Safety configuration must keep paper executor shadow-only at startup.")
    cfg = ExecutorConfig(
        mode=mode,
        runtime_root=runtime_root,
        database_path=database_path,
        server=server,
        scanner=ScannerConfig(
            accepted_types=tuple(scanner_data.get("accepted_types", ("flash_agentic", "flash"))),
            episode_cooldown_minutes=int(scanner_data.get("episode_cooldown_minutes", 10)),
            maximum_signal_age_seconds=int(scanner_data.get("maximum_signal_age_seconds", 120)),
            maximum_level_distance_pct=float(scanner_data.get("maximum_level_distance_pct", 20.0)),
        ),
        strategy=StrategyConfig(
            allowed_setups={
                str(k): tuple(v) for k, v in strategy_data.get("allowed_setups", {
                    "flash_agentic": (),
                    "flash": ("floor bounce",),
                }).items()
            },
            allowed_tickers=tuple(str(t).upper() for t in strategy_data.get("allowed_tickers", ("NVDA", "GOOGL", "AVGO"))),
            entry_window_et_start=strategy_data.get("entry_window_et_start"),
            entry_window_et_end=strategy_data.get("entry_window_et_end"),
            allowed_patterns=tuple(
                {
                    "scanner_type": str(p.get("scanner_type") or p.get("scanner") or "").lower(),
                    "setup": str(p.get("setup") or "").lower(),
                    "direction": str(p.get("direction") or "").lower(),
                }
                for p in strategy_data.get("allowed_patterns", ())
                if isinstance(p, dict)
            ),
        ),
        market_data=MarketDataConfig(**{**MarketDataConfig().__dict__, **market_data}),
        instrument=InstrumentConfig(**{**InstrumentConfig().__dict__, **instrument_data}),
        contract=ContractConfig(**{**ContractConfig().__dict__, **contract_data}),
        portfolio=PortfolioConfig(**{**PortfolioConfig().__dict__, **portfolio_data}),
        exit=ExitConfig(**{**ExitConfig().__dict__, **exit_data}),
        simulation=SimulationConfig(**{**SimulationConfig().__dict__, **simulation_data}),
        vm_forwarding=VmForwardingConfig(**{**VmForwardingConfig().__dict__, **vm_data}),
        safety=safety,
    )
    if cfg.mode not in {Mode.DISABLED, Mode.SHADOW, Mode.PAPER}:
        raise ValueError("Invalid executor mode.")
    if cfg.market_data.provider != "tradier_production":
        raise ValueError("Only Tradier production market data is supported.")
    return cfg
