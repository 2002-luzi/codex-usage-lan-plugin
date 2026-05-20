#!/usr/bin/env python3
"""Simple HTTP client for Codex Usage LAN."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def request_json(url: str, token: str = "") -> None:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")

    print(f"{url} -> HTTP {status}")
    try:
        print(json.dumps(json.loads(body), indent=2, sort_keys=True))
    except json.JSONDecodeError:
        print(body)


def healthz_url(data_url: str) -> str:
    parsed = urllib.parse.urlparse(data_url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/healthz", "", "", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Codex Usage LAN HTTP endpoints")
    parser.add_argument("--url", default="http://127.0.0.1:8000/data.json", help="data.json URL")
    args = parser.parse_args()

    token = os.environ.get("CODEX_USAGE_LAN_TOKEN", "")
    request_json(healthz_url(args.url))
    request_json(args.url, token=token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
