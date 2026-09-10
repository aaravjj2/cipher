from __future__ import annotations

import json
import math
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
    # Opt-in premarket-entry mode for the autopilot: cipher-scanner cards may
    # enter before the regular window opens (only during premarket hours; the
    # window close still binds). All other scanner types are unaffected.
    allow_premarket_entries: bool = False
    allowed_patterns: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class InstrumentConfig:
    model: str = "long_option"
    maximum_spread_width: float = 10.0
    minimum_spread_width: float = 1.0


@dataclass(frozen=True)
class MarketDataConfig:
    provider: str = "tradier_production"
    core_url: str = "http://127.0.0.1:8282"
    request_timeout_seconds: int = 90
    chain_expiration_count: int = 6
    chain_cache_seconds: int = 30
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
    maximum_new_positions_per_ticker_per_day: int = 5
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
    fee_per_contract: float = 0.0

    def __post_init__(self):
        if not self.entry_at_ask or not self.exit_at_bid:
            raise ValueError("Only observed ask entries and bid exits are supported")
        if any(not math.isfinite(value) or value < 0 for value in (self.minimum_slippage_dollars, self.slippage_pct, self.fee_per_contract)):
            raise ValueError("Simulation costs must be finite and nonnegative")


@dataclass(frozen=True)
class ExperimentConfig:
    cohort_id: str = "legacy"
    version: str = "v1"
    registry_strategy_id: str | None = None
    confirmation_observations: int = 1
    maximum_round_trip_stop_fraction: float | None = None
    take_profit_remaining_fraction: float | None = None

    def __post_init__(self):
        if self.confirmation_observations < 1:
            raise ValueError("confirmation_observations must be positive")
        for value in (self.maximum_round_trip_stop_fraction, self.take_profit_remaining_fraction):
            if value is not None and not 0 < value <= 1:
                raise ValueError("experiment fractions must be in (0, 1]")


@dataclass(frozen=True)
class ExecutionConfig:
    backend: str = "simulated"
    order_timeout_seconds: int = 20
    poll_interval_seconds: float = 1.0
    auto_promote_paper: bool = False
    paper_forward_test_authorized: bool = False


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
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    vm_forwarding: VmForwardingConfig = field(default_factory=VmForwardingConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    @property
    def kill_switch_path(self) -> Path:
        return self.runtime_root / "STOP_PAPER_EXECUTOR"


_TOP = {
    "mode", "runtime_root", "database_path", "server", "scanner", "strategy",
    "market_data", "instrument", "contract", "portfolio", "exit", "simulation", "execution",
    "vm_forwarding", "safety", "experiment",
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
    execution_data = data.get("execution", {})
    vm_data = data.get("vm_forwarding", {})
    safety_data = data.get("safety", {})
    for name, section in {
        "server": server_data, "scanner": scanner_data, "strategy": strategy_data,
        "market_data": market_data, "instrument": instrument_data, "contract": contract_data, "portfolio": portfolio_data,
        "exit": exit_data, "simulation": simulation_data, "vm_forwarding": vm_data,
        "execution": execution_data,
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
            allow_premarket_entries=bool(strategy_data.get("allow_premarket_entries", False)),
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
        execution=ExecutionConfig(**{**ExecutionConfig().__dict__, **execution_data}),
        experiment=ExperimentConfig(**data.get("experiment", {})),
        vm_forwarding=VmForwardingConfig(**{**VmForwardingConfig().__dict__, **vm_data}),
        safety=safety,
    )
    if cfg.mode not in {Mode.DISABLED, Mode.SHADOW, Mode.PAPER}:
        raise ValueError("Invalid executor mode.")
    if cfg.market_data.provider not in {"tradier_production", "alpaca_core"}:
        raise ValueError("Market data provider must be tradier_production or alpaca_core.")
    if cfg.market_data.provider == "alpaca_core" and not cfg.market_data.core_url.startswith("http://127.0.0.1:"):
        raise ValueError("Alpaca core market data must use a loopback Cipher API URL.")
    if cfg.execution.backend not in {"simulated", "alpaca_paper"}:
        raise ValueError("Execution backend must be simulated or alpaca_paper.")
    if cfg.execution.backend == "alpaca_paper" and cfg.instrument.model != "long_option":
        raise ValueError("Alpaca paper execution currently supports long_option only.")
    if cfg.execution.order_timeout_seconds < 1 or cfg.execution.poll_interval_seconds <= 0:
        raise ValueError("Execution polling values must be positive.")
    return cfg
