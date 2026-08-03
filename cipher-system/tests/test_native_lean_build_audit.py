from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_native_lean_build.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("native_lean_build_audit_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flatten_vulnerabilities_preserves_package_and_severity():
    module = _load_module()
    payload = {
        "projects": [
            {
                "frameworks": [
                    {
                        "framework": "net10.0",
                        "transitivePackages": [
                            {
                                "id": "Example.Package",
                                "resolvedVersion": "1.2.3",
                                "vulnerabilities": [
                                    {
                                        "severity": "High",
                                        "advisoryurl": "https://example.invalid/advisory",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }
    assert module.flatten_vulnerabilities(payload) == [
        {
            "package": "Example.Package",
            "version": "1.2.3",
            "severity": "High",
            "advisory_url": "https://example.invalid/advisory",
        }
    ]


def test_build_audit_records_compiled_launcher_without_running_strategy(tmp_path, monkeypatch):
    module = _load_module()
    lean_root = tmp_path / "Lean"
    project = lean_root / "Launcher" / "QuantConnect.Lean.Launcher.csproj"
    launcher = lean_root / "Launcher" / "bin" / "Release" / "QuantConnect.Lean.Launcher.dll"
    dotnet = tmp_path / "dotnet"
    project.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    project.write_text("<Project />", encoding="utf-8")
    launcher.write_bytes(b"compiled lean launcher")
    dotnet.write_text("binary", encoding="utf-8")

    vulnerability_json = """{
      "projects": [{
        "frameworks": [{
          "transitivePackages": [{
            "id": "Unsafe.Package",
            "resolvedVersion": "4.0.0",
            "vulnerabilities": [{
              "severity": "Critical",
              "advisoryurl": "https://example.invalid/critical"
            }]
          }]
        }]
      }]
    }"""

    def fake_run(command, *, timeout=120):
        if command[:3] == ["git", "-C", str(lean_root)]:
            return {"ok": True, "returncode": 0, "stdout": "a" * 40, "stderr": ""}
        if command == [str(dotnet), "--version"]:
            return {"ok": True, "returncode": 0, "stdout": "10.0.302", "stderr": ""}
        assert "--vulnerable" in command
        return {"ok": True, "returncode": 0, "stdout": vulnerability_json, "stderr": ""}

    monkeypatch.setattr(module, "run", fake_run)
    payload = module.build_audit(lean_root, dotnet=dotnet)

    assert payload["native_build_ready"] is True
    assert payload["launcher"]["sha256"]
    assert payload["vulnerability_audit"]["severity_counts"] == {"Critical": 1}
    assert payload["vulnerability_audit"]["security_clean"] is False
    assert payload["strategy_or_backtest_run"] is False
    assert payload["brokerage_connection_attempted"] is False
    assert payload["cloud_login_attempted"] is False
    assert payload["promotion_eligible"] is False
    assert payload["execution_authority"] is False


def test_build_audit_fails_closed_when_launcher_is_absent(tmp_path, monkeypatch):
    module = _load_module()
    dotnet = tmp_path / "dotnet"
    dotnet.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "run",
        lambda command, timeout=120: {
            "ok": True,
            "returncode": 0,
            "stdout": "10.0.302" if command[-1] == "--version" else "{}",
            "stderr": "",
        },
    )
    payload = module.build_audit(tmp_path / "missing-lean", dotnet=dotnet)
    assert payload["native_build_ready"] is False
    assert payload["launcher"]["exists"] is False
    assert payload["promotion_eligible"] is False
