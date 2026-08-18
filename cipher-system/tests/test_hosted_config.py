from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_hosted_public_variables_are_explicit_and_secret_free():
    env = (WEB / ".env.hosted.example").read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_SUPABASE_URL=" in env
    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY=" in env
    assert "NEXT_PUBLIC_CIPHER_API_ORIGIN=https://" in env
    assert "SUPABASE_SERVICE_ROLE_KEY=" not in env
    assert "CIPHER_INTERNAL_PROXY_TOKEN=" not in env
    assert "ALPACA_API_SECRET=" not in env
    assert "ALPACA_ALGO_SECRET=" not in env


def test_vercel_config_contains_no_credentials_or_core_port():
    config = (WEB / "vercel.json").read_text(encoding="utf-8")
    assert "SUPABASE" not in config
    assert "ALPACA" not in config
    assert ":8282" not in config
    assert "X-Content-Type-Options" in config


def test_static_export_is_preserved_for_local_publish():
    next_config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    assert 'output: "export"' in next_config
    assert (WEB / "deploy.sh").is_file()


def test_hosted_docs_define_manual_api_origin_and_session_only_credentials():
    docs = (ROOT / "docs/provider-compatibility.md").read_text(encoding="utf-8")
    assert "stable HTTPS API hostname" in docs
    assert "session-only" in docs
    assert "service-role key" in docs
