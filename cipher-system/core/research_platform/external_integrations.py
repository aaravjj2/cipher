from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .models import AllowedUse, PromotionState


DEFAULT_EXTERNAL_ROOT = Path("/home/aarav/Aarav/Autopilot/external")


@dataclass(frozen=True)
class ExternalRepoIntegration:
    name: str
    relative_path: str
    source_url: str
    commit: str
    layer: int
    role: str
    allowed_use: tuple[AllowedUse, ...]
    activation: str
    live_runtime_enabled: bool
    blocked_capabilities: tuple[str, ...] = ()
    license: str = "unknown"
    notes: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolved_path(self, root: str | Path = DEFAULT_EXTERNAL_ROOT) -> Path:
        return Path(root) / self.relative_path

    def to_dict(self, root: str | Path = DEFAULT_EXTERNAL_ROOT) -> dict[str, Any]:
        path = self.resolved_path(root)
        return {
            "name": self.name,
            "path": str(path),
            "available": path.exists(),
            "source_url": self.source_url,
            "commit": self.commit,
            "layer": self.layer,
            "role": self.role,
            "allowed_use": [item.value for item in self.allowed_use],
            "activation": self.activation,
            "live_runtime_enabled": self.live_runtime_enabled,
            "blocked_capabilities": list(self.blocked_capabilities),
            "license": self.license,
            "notes": self.notes,
            "metadata": dict(self.metadata),
            "maximum_promotion_state": PromotionState.LIVE_REVIEW_REQUIRED.value,
            "broker_order_authority": False,
        }


DEFAULT_EXTERNAL_INTEGRATIONS: tuple[ExternalRepoIntegration, ...] = (
    ExternalRepoIntegration(
        name="MiroFish",
        relative_path="MiroFish",
        source_url="https://github.com/666ghj/MiroFish.git",
        commit="17d0a91",
        layer=7,
        role="swarm-intelligence scenario rehearsal for autoresearch context",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="offline_adapter_planned",
        live_runtime_enabled=False,
        blocked_capabilities=("direct_runtime_prompt_mutation", "live_trade_recommendation"),
        license="AGPL-3.0",
        notes="Use as sandboxed report/context generator only; do not run as an always-on Cipher service.",
    ),
    ExternalRepoIntegration(
        name="TradingAgents",
        relative_path="TradingAgents",
        source_url="https://github.com/TauricResearch/TradingAgents.git",
        commit="7e9e7b8",
        layer=6,
        role="multi-agent analyst-panel reference for context memos",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="offline_reference_only",
        live_runtime_enabled=False,
        blocked_capabilities=("final_trade_decision_as_order", "portfolio_manager_live_authority"),
        notes="Research-purpose analyst debate output may be converted to ContextPanel memos only.",
    ),
    ExternalRepoIntegration(
        name="Dexter",
        relative_path="dexter",
        source_url="https://github.com/virattt/dexter.git",
        commit="4c355d4",
        layer=6,
        role="financial research agent reference for task planning and source gathering",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="offline_reference_only",
        live_runtime_enabled=False,
        blocked_capabilities=("autonomous_execution_against_cipher_runtime", "trade_sizing_authority"),
        notes="Can inspire research task plans; outputs require Cipher validation and human review.",
    ),
    ExternalRepoIntegration(
        name="financial-services",
        relative_path="financial-services",
        source_url="https://github.com/anthropics/financial-services.git",
        commit="57772c3",
        layer=7,
        role="human-review financial workflow templates and model/reconciliation skills",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="template_reference_only",
        live_runtime_enabled=False,
        blocked_capabilities=("investment_recommendation_authority", "ledger_posting", "transaction_approval"),
        notes="Use templates for reviewed artifacts, not autonomous trading decisions.",
    ),
    ExternalRepoIntegration(
        name="daily_stock_analysis",
        relative_path="daily_stock_analysis",
        source_url="https://github.com/ZhuLinsen/daily_stock_analysis.git",
        commit="8816421",
        layer=2,
        role="external news/sentiment and daily equity analysis reference pipeline",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="offline_ingestion_reference",
        live_runtime_enabled=False,
        blocked_capabilities=("direct_signal_promotion", "external_api_direct_to_execution"),
        notes="External analysis can be frozen as research input, then must pass Cipher data-quality gates.",
    ),
    ExternalRepoIntegration(
        name="PolyMarket-MCP",
        relative_path="PolyMarket-MCP",
        source_url="https://github.com/guangxiangdebizi/PolyMarket-MCP.git",
        commit="154d74d",
        layer=1,
        role="prediction-market read-only market data reference",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="blocked_from_active_mcp",
        live_runtime_enabled=False,
        blocked_capabilities=("order_creation", "prediction_market_trading", "wallet_or_key_access"),
        notes="Only market/event data ideas are admissible; do not expose MCP tools to Cipher runtime.",
    ),
    ExternalRepoIntegration(
        name="polymarket-mcp-server",
        relative_path="polymarket-mcp-server",
        source_url="https://github.com/caiovicentino/polymarket-mcp-server.git",
        commit="daa8243",
        layer=1,
        role="prediction-market analytics reference with trading tools blocked",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="blocked_from_active_mcp",
        live_runtime_enabled=False,
        blocked_capabilities=("create_limit_order", "create_market_order", "execute_smart_trade", "cancel_order"),
        notes="Repository advertises trading tools; keep disabled except for manual code review of read-only data concepts.",
    ),
    ExternalRepoIntegration(
        name="alpaca-mcp-server",
        relative_path="alpaca-mcp-server",
        source_url="https://github.com/alpacahq/alpaca-mcp-server.git",
        commit="74fdfb7",
        layer=1,
        role="Alpaca API reference with order tools blocked",
        allowed_use=(AllowedUse.CONTEXT,),
        activation="blocked_from_active_mcp",
        live_runtime_enabled=False,
        blocked_capabilities=("place_stock_order", "place_option_order", "place_crypto_order", "cancel_all_orders"),
        notes="Cipher uses its own server-side read-only market-data path; do not attach this MCP to active runtime.",
    ),
)


def integration_status(root: str | Path = DEFAULT_EXTERNAL_ROOT) -> dict[str, Any]:
    integrations = [item.to_dict(root) for item in DEFAULT_EXTERNAL_INTEGRATIONS]
    violations = []
    for item in integrations:
        if item["live_runtime_enabled"]:
            violations.append({"name": item["name"], "reason": "external_repo_live_runtime_enabled"})
        if "execution" in item["allowed_use"]:
            violations.append({"name": item["name"], "reason": "execution_allowed_use_forbidden"})
        if item["broker_order_authority"]:
            violations.append({"name": item["name"], "reason": "broker_order_authority_forbidden"})
    return {
        "root": str(Path(root)),
        "integrations": integrations,
        "available_count": sum(1 for item in integrations if item["available"]),
        "total_count": len(integrations),
        "boundary_violations": violations,
        "fully_registered": len(integrations) == len(DEFAULT_EXTERNAL_INTEGRATIONS),
        "usable_now": not violations,
    }
