from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_cloudflare_token_is_written_atomically_and_privately(tmp_path: Path):
    sync = load(REPO / "infra/gcp-cipher-vm/bin/sync-secrets.py", "sync_secrets")
    target = tmp_path / "token"
    sync.write_private_file(target, "secret\n")
    assert target.read_text(encoding="utf-8") == "secret\n"
    assert target.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".token.tmp").exists()


def test_secret_sync_makes_local_auth_mode_explicit():
    sync = load(REPO / "infra/gcp-cipher-vm/bin/sync-secrets.py", "sync_secrets")
    with_password = sync.render_env({"CIPHER_APP_PASSWORD_HASH": "scrypt$16384$8$1$aa$bb"}, {})
    assert "CIPHER_APP_AUTH=on" in with_password
    assert "CIPHER_APP_AUTH=off" not in with_password
    assert "CIPHER_APP_AUTH=off" in sync.render_env({}, {})


def test_secret_sync_preserves_keys_it_does_not_manage(tmp_path: Path):
    """The regression that narrowed the Tradier capture from 23 underlyings to 3.

    The env file is rebuilt from scratch each run, so a hand-set TRADIER_STREAM_SYMBOLS
    was deleted and run-tradier-loop.sh silently fell back to `SPY,QQQ,IWM`.
    """
    sync = load(REPO / "infra/gcp-cipher-vm/bin/sync-secrets.py", "sync_secrets")
    symbols = "SPY,QQQ,IWM,NFLX,COST,JPM,XOM,WMT,UNH,LLY,V,MA,NVDA"
    existing = {"TRADIER_STREAM_SYMBOLS": symbols, "PORT": "9999"}

    rendered = sync.render_env({"TRADIER_ACCESS_TOKEN": "tok"}, existing)

    assert f"TRADIER_STREAM_SYMBOLS='{symbols}'" in rendered
    # A managed key is still owned by this script, not inherited from the old file.
    assert "PORT=8283" in rendered
    assert "PORT=9999" not in rendered
    # Round-trips: what we write parses back to what we meant.
    target = tmp_path / "cipher.env"
    sync.write_private_file(target, rendered)
    assert sync.parse_env_file(target)["TRADIER_STREAM_SYMBOLS"] == symbols


def test_secret_sync_never_downgrades_a_configured_password(monkeypatch):
    """An unreachable Secret Manager must not switch the login gate off."""
    sync = load(REPO / "infra/gcp-cipher-vm/bin/sync-secrets.py", "sync_secrets")
    monkeypatch.setattr(sync, "PROJECT_ID", "test-project")

    class Unreachable:
        def access_secret_version(self, request):  # noqa: ANN001, ANN202
            raise RuntimeError("secret manager unavailable")

    existing = {"CIPHER_APP_PASSWORD_HASH": "scrypt$16384$8$1$aa$bb", "LSE_API_KEY": "key"}
    resolved = sync.resolve_secrets(Unreachable(), existing)

    assert resolved["CIPHER_APP_PASSWORD_HASH"] == existing["CIPHER_APP_PASSWORD_HASH"]
    assert resolved["LSE_API_KEY"] == "key"
    assert "CIPHER_APP_AUTH=on" in sync.render_env(resolved, existing)


def test_secret_sync_quotes_values_that_would_break_the_env_file():
    sync = load(REPO / "infra/gcp-cipher-vm/bin/sync-secrets.py", "sync_secrets")
    nasty = "it's a $(value) with spaces"
    rendered = sync.render_env({}, {"CIPHER_NOTE": nasty})
    line = next(l for l in rendered.splitlines() if l.startswith("CIPHER_NOTE="))
    assert sync.unquote(line.partition("=")[2]) == nasty


def test_access_verifier_accepts_only_access_redirects_or_denials():
    verifier = load(
        REPO / "infra/gcp-cipher-vm/bin/verify-cloudflare-access.py", "cf_verify"
    )
    verifier.assert_access_blocked(
        verifier.Probe("/", 302, "https://team.cloudflareaccess.com/cdn-cgi/access/login", "text/html", b""),
        "cipher.example.com",
    )
    verifier.assert_access_blocked(
        verifier.Probe("/api/health", 403, "", "text/html", b"denied"),
        "cipher.example.com",
    )
    with pytest.raises(AssertionError, match="returned 200"):
        verifier.assert_access_blocked(
            verifier.Probe("/api/quote", 200, "", "application/json", b'{"spot": 1}'),
            "cipher.example.com",
        )
    with pytest.raises(AssertionError, match="outside Cloudflare Access"):
        verifier.assert_access_blocked(
            verifier.Probe("/", 302, "https://evil.example/login", "text/html", b""),
            "cipher.example.com",
        )


def test_connector_starts_only_after_password_gated_web_restart():
    configure = (REPO / "infra/gcp-cipher-vm/configure-vm.sh").read_text(encoding="utf-8")
    app_restart = configure.index("for svc in cipher-core cipher-web")
    connector_start = configure.index("enable --now cipher-cloudflared.service")
    assert app_restart < connector_start
    assert "grep -q '^CIPHER_APP_PASSWORD_HASH='" in configure
    assert "systemctl is-active --quiet cipher-web.service" in configure

    unit = (REPO / "infra/gcp-cipher-vm/systemd/cipher-cloudflared.service").read_text(
        encoding="utf-8"
    )
    assert "LoadCredential=tunnel-token:" in unit
    assert "--token-file %d/tunnel-token" in unit
    assert "--token " not in unit
