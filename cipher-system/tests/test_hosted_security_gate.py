from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
WEB = ROOT / "web"


def test_hosted_proxy_fails_closed_and_only_exposes_liveness_without_auth():
    source = (APP / "server.mjs").read_text(encoding="utf-8")
    assert 'const hostedMode = process.env.CIPHER_HOSTED === "1"' in source
    assert 'return sendJson(res, 401, { error: "authentication required" }' in source
    assert 'if (!userContext) return sendJson(res, 200, { status: "ok" }' in source
    assert 'CIPHER_INTERNAL_PROXY_TOKEN' in source
    assert 'trustedCoreHeaders(coreUserContext)' in source
    assert 'headers["x-cipher-guest"] = "1"' in source
    assert '"/api/chart-saves": "/api/chart-saves"' in source
    assert '"/api/standing-notes": "/api/standing-notes"' in source


def test_browser_hosted_configuration_has_no_private_credentials():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            WEB / ".env.hosted.example",
            WEB / "vercel.json",
            WEB / "src/lib/supabase.ts",
            WEB / "src/lib/auth.ts",
            WEB / "src/lib/api.ts",
        ]
    )
    assert "SUPABASE_SERVICE_ROLE_KEY=" not in source
    assert "CIPHER_INTERNAL_PROXY_TOKEN=" not in source
    assert "ALPACA_API_SECRET=" not in source
    assert "ALPACA_ALGO_SECRET=" not in source
    assert "localStorage" not in (WEB / "src/components/auth/ProviderConnectionPanel.tsx").read_text(encoding="utf-8")
    assert "sessionStorage" not in (WEB / "src/components/auth/ProviderConnectionPanel.tsx").read_text(encoding="utf-8")


def test_no_live_order_authority_is_added_to_hosted_files():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [APP / "server.mjs", APP / "provider_session_client.mjs", APP / "supabase_auth.mjs"]
    )
    for forbidden in ("TradingClient", "OrderClient", "submit_order", "place_order", "create_order"):
        assert forbidden not in source
