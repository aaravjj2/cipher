#!/usr/bin/env python3
"""Fail closed unless unauthenticated Cloudflare requests reveal no Cipher data."""
from __future__ import annotations

import argparse
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True)
class Probe:
    path: str
    status: int
    location: str
    content_type: str
    body: bytes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def probe(base: str, path: str, timeout: float = 15.0) -> Probe:
    request = urllib.request.Request(
        urljoin(base, path),
        headers={"User-Agent": "cipher-cloudflare-access-verifier/1"},
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect()
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    body = response.read(64 * 1024)
    return Probe(
        path=path,
        status=int(response.status),
        location=str(response.headers.get("location") or ""),
        content_type=str(response.headers.get("content-type") or ""),
        body=body,
    )


def assert_access_blocked(result: Probe, hostname: str) -> None:
    if result.status == 200:
        raise AssertionError(f"{result.path} returned 200 without Access authentication")
    if result.status not in {301, 302, 303, 307, 308, 401, 403}:
        raise AssertionError(f"{result.path} returned unexpected status {result.status}")
    if result.status in {301, 302, 303, 307, 308}:
        redirect = urlparse(urljoin(f"https://{hostname}", result.location))
        access_redirect = (
            redirect.hostname == hostname and redirect.path.startswith("/cdn-cgi/access/")
        ) or str(redirect.hostname or "").endswith(".cloudflareaccess.com")
        if not access_redirect:
            raise AssertionError(f"{result.path} redirects outside Cloudflare Access")
    lowered = result.body.lower()
    if b'"spot"' in lowered or b'"market_data_configured"' in lowered:
        raise AssertionError(f"{result.path} exposed Cipher market-data JSON")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    args = parser.parse_args()
    hostname = args.hostname.strip().lower().rstrip(".")
    if not hostname or "://" in hostname or "/" in hostname:
        raise SystemExit("--hostname must be a bare DNS hostname")
    base = f"https://{hostname}"
    results = [probe(base, path) for path in ("/", "/api/health", "/api/quote?ticker=SPY")]
    for result in results:
        assert_access_blocked(result, hostname)
        print(f"PASS {result.path}: status={result.status} access_redirect={bool(result.location)}")
    print("Unauthenticated Cloudflare boundary is fail-closed; no Cipher data was returned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
