#!/usr/bin/env python3
"""Public HTTPS MCP smoke check. Reads the operator token locally, never prints it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://cipher-main.tail39504f.ts.net:10000/mcp")
    parser.add_argument("--token-file", type=Path, default=Path("/home/aarav/Aarav/cipher/runtime/config/mcp-bearer-token.txt"))
    parser.add_argument("--all", action="store_true", help="Exercise every listed read-only tool (can take several minutes)")
    parser.add_argument("--oauth", action="store_true", help="Also exercise public DCR, consent, PKCE and refresh (creates a verification grant)")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("public connector must use HTTPS")
    token = args.token_file.read_text().strip()
    assert token, "operator token missing"
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(NoRedirect)
    def request(url, payload=None, authenticated=False):
        headers = {"Accept": "application/json, text/event-stream"}
        if authenticated: headers["Authorization"] = "Bearer " + token
        if payload is not None: headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with opener.open(req, timeout=65) as response: return json.load(response)
    def rpc(method, params):
        response = request(args.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, True)
        assert "error" not in response, "JSON-RPC error"
        return response["result"]
    try:
        request(args.url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        raise AssertionError("unauthenticated request accepted")
    except urllib.error.HTTPError as error:
        assert error.code == 401
        assert "resource_metadata=" in error.headers.get("WWW-Authenticate", "")
    base = args.url.rsplit("/mcp", 1)[0]
    metadata = request(base + "/.well-known/oauth-protected-resource")
    assert metadata["resource"] == args.url
    auth = request(base + "/.well-known/oauth-authorization-server")
    assert "S256" in auth["code_challenge_methods_supported"]
    assert auth["registration_endpoint"].startswith(base + "/")
    if args.oauth:
        callback = "http://127.0.0.1:9/cipher-deployment-check"
        client = request(base + "/register", {"redirect_uris": [callback], "client_name": "Cipher deployment verification"})
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        try:
            request(base + "/authorize", {"client_id": client["client_id"], "redirect_uri": callback,
                "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256",
                "state": state, "scope": "cipher.read", "resource": args.url, "secret": token})
            raise AssertionError("authorization did not redirect")
        except urllib.error.HTTPError as error:
            assert error.code == 302, "operator consent failed"
            redirect = urllib.parse.urlsplit(error.headers["Location"])
            assert urllib.parse.urlunsplit(redirect._replace(query="")) == callback
            query = urllib.parse.parse_qs(redirect.query)
            assert query["state"] == [state]
            code = query["code"][0]
        grant = request(base + "/token", {"grant_type": "authorization_code", "code": code,
            "client_id": client["client_id"], "redirect_uri": callback,
            "code_verifier": verifier, "resource": args.url})
        refreshed = request(base + "/token", {"grant_type": "refresh_token", "refresh_token": grant["refresh_token"],
            "client_id": client["client_id"], "resource": args.url})
        token = refreshed["access_token"]
        print("PASS public OAuth DCR / operator consent / PKCE / refresh", flush=True)
    rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "cipher-public-check", "version": "1"}})
    tools = rpc("tools/list", {})["tools"]
    names = [tool["name"] for tool in tools]
    assert len(names) == len(set(names))
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    print(f"PASS HTTPS / auth rejection / OAuth discovery / initialize / {len(tools)} tools", flush=True)
    selected = names if args.all else ["get_quote", "get_data_freshness", "get_paper_portfolios", "autopilot_status"]
    failures = []
    for name in selected:
        spec = next(tool for tool in tools if tool["name"] == name)
        properties = spec["inputSchema"]["properties"]
        values = {"symbol": "SPY", "query": "SPY", "id": "SPY", "strike": 770}
        inputs = {key: value for key, value in values.items() if key in properties}
        try:
            result = rpc("tools/call", {"name": name, "arguments": inputs})
            if result.get("isError"): raise ValueError("tool returned isError")
            data = result.get("structuredContent", {})
            if data.get("error"): raise ValueError("tool returned data error")
            print(f"PASS {name}" + (" (bounded text result)" if "structuredContent" not in result else ""), flush=True)
            if name == "get_quote": print(json.dumps({key: data.get(key) for key in ("feed", "as_of")}), flush=True)
            if name == "get_data_freshness": print(json.dumps({"healthy": data.get("healthy"), "exceptions": data.get("exceptions")}), flush=True)
        except Exception as error:
            failures.append(name)
            print(f"FAIL {name}: {type(error).__name__}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
