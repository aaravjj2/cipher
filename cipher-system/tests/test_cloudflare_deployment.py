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
    source = (REPO / "infra/gcp-cipher-vm/bin/sync-secrets.py").read_text(encoding="utf-8")
    assert "f\"CIPHER_APP_AUTH={'on' if password_hash_configured else 'off'}\"" in source


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
