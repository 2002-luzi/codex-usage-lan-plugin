#!/usr/bin/env python3
"""Codex Usage LAN MCP server and HTTP exporter.

stdout is reserved for newline-delimited JSON-RPC responses. All logs go to
stderr so the MCP stdio transport stays clean.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import functools
import http.server
import json
import os
import pathlib
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Tuple


SERVER_NAME = "codex-usage-lan"
SERVER_VERSION = "0.1.0"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", file=sys.stderr, flush=True)


def compact_home(path: pathlib.Path) -> str:
    try:
        return "~/" + str(path.expanduser().resolve().relative_to(pathlib.Path.home().resolve()))
    except Exception:
        return str(path)


def normalize_status_output(data: str) -> str:
    data = data.replace("\r\n", "\n").replace("\r", "\n").replace("\u2502", "|")
    data = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])", "", data)
    return "".join(ch for ch in data if ch in "\n\t" or ord(ch) >= 32)


def first_matching_line(text: str, pattern: str) -> str:
    regex = re.compile(pattern, re.IGNORECASE)
    for line in text.splitlines():
        if regex.search(line):
            return line.strip()
    return ""


def limit_segment(text: str, label: str) -> str:
    label_re = re.compile(re.escape(label), re.IGNORECASE)
    for line in text.splitlines():
        match = label_re.search(line)
        if not match:
            continue
        segment = line[match.start() :].strip()
        other_labels = ["5h limit", "weekly limit"]
        for other in other_labels:
            if other.lower() == label.lower():
                continue
            other_match = re.search(re.escape(other), segment[1:], re.IGNORECASE)
            if other_match:
                segment = segment[: other_match.start() + 1].strip()
        return segment
    return ""


def parse_limit_line(line: str) -> Tuple[Optional[int], str]:
    pct_match = re.search(r"(\d+)\s*%\s*left", line, re.IGNORECASE)
    reset_match = re.search(r"resets\s+(.+?)(?:\)|\s*\||$)", line, re.IGNORECASE)
    pct = int(pct_match.group(1)) if pct_match else None
    reset = reset_match.group(1).strip() if reset_match else "unknown"
    return pct, reset or "unknown"


def parse_label_line(line: str, label: str) -> str:
    if not line:
        return ""
    match = re.search(rf"{re.escape(label)}:\s*(.*)", line, re.IGNORECASE)
    if not match:
        return ""
    value = re.split(r"\s*\|\s*", match.group(1), maxsplit=1)[0].strip()
    return value


def parse_codex_status(raw: str, interval_seconds: int) -> Dict[str, Any]:
    cleaned = normalize_status_output(raw)
    if not cleaned.strip():
        raise ValueError("codex /status returned empty output")

    five_h_line = limit_segment(cleaned, "5h limit")
    weekly_line = limit_segment(cleaned, "weekly limit")
    model_line = first_matching_line(cleaned, r"\bModel:")
    account_line = first_matching_line(cleaned, r"\bAccount:")

    five_h_pct, five_h_reset = parse_limit_line(five_h_line)
    weekly_pct, weekly_reset = parse_limit_line(weekly_line)

    if five_h_pct is None and weekly_pct is None:
        raise ValueError("could not parse usage percentages from codex status output")

    return {
        "five_h_pct": five_h_pct if five_h_pct is not None else 0,
        "five_h_reset": five_h_reset,
        "weekly_pct": weekly_pct if weekly_pct is not None else 0,
        "weekly_reset": weekly_reset,
        "model": parse_label_line(model_line, "Model") or "unknown",
        "account": parse_label_line(account_line, "Account") or "",
        "scraped_at": utc_now(),
        "sample_interval_seconds": int(interval_seconds),
    }


def run_codex_status_command(timeout_seconds: int) -> str:
    codex_cmd = os.environ.get("CODEX_BIN", "codex")
    deadline = time.monotonic() + max(5, timeout_seconds)
    next_status_attempt = time.monotonic() + 4
    status_attempts = 0
    max_status_attempts = 4
    trust_ack_sent = False
    buffer: List[str] = []

    if not hasattr(os, "openpty"):
        proc = subprocess.run(
            [codex_cmd, "/status"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        return proc.stdout

    master_fd, slave_fd = os.openpty()
    proc: Optional[subprocess.Popen[bytes]] = None
    try:
        proc = subprocess.Popen(
            [codex_cmd, "--no-alt-screen"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="ignore")
                buffer.append(text)
                normalized = "".join(buffer).replace("\r", "\n")

                if (
                    not trust_ack_sent
                    and (
                        "Do you trust the contents of this directory" in normalized
                        or "Press enter to continue" in normalized
                        or "prompt injection" in normalized
                    )
                ):
                    os.write(master_fd, b"1\r")
                    trust_ack_sent = True
                    next_status_attempt = time.monotonic() + 3
                    continue

                if status_attempts < max_status_attempts and time.monotonic() >= next_status_attempt:
                    os.write(master_fd, b"/status\r")
                    status_attempts += 1
                    next_status_attempt = time.monotonic() + 2

                if re.search(r"5h limit", normalized, re.IGNORECASE) and re.search(
                    r"weekly limit", normalized, re.IGNORECASE
                ):
                    time.sleep(0.5)
                    break
            elif status_attempts < max_status_attempts and time.monotonic() >= next_status_attempt:
                os.write(master_fd, b"/status\r")
                status_attempts += 1
                next_status_attempt = time.monotonic() + 2
    finally:
        if proc is not None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        os.close(master_fd)

    output = "".join(buffer)
    debug_path = os.environ.get("CODEX_STATUS_DEBUG_FILE")
    if debug_path:
        pathlib.Path(debug_path).expanduser().write_text(output, encoding="utf-8")
    return output


def iter_session_candidates(codex_dir: pathlib.Path) -> Iterable[pathlib.Path]:
    direct = [
        codex_dir / "session_index.jsonl",
        codex_dir / "history.jsonl",
        codex_dir / "log" / "codex-tui.log",
    ]
    for path in direct:
        if path.exists():
            yield path

    for subdir in ("sessions", "archived_sessions"):
        root = codex_dir / subdir
        if root.exists():
            yield from root.rglob("*.jsonl")


def scan_session_files(limit: int = 12) -> Dict[str, Any]:
    codex_dir = pathlib.Path(os.environ.get("CODEX_USAGE_LAN_SESSION_DIR", "~/.codex")).expanduser()
    files: List[Dict[str, Any]] = []
    total_bytes = 0

    for path in iter_session_candidates(codex_dir):
        try:
            stat = path.stat()
        except OSError:
            continue
        total_bytes += stat.st_size
        files.append(
            {
                "path": compact_home(path),
                "size_bytes": stat.st_size,
                "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )

    files.sort(key=lambda item: item["modified_at"], reverse=True)
    return {
        "codex_dir": compact_home(codex_dir),
        "files_count": len(files),
        "total_bytes": total_bytes,
        "recent_files": files[:limit],
    }


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def url_host(host: str) -> str:
    if host in ("0.0.0.0", "::", ""):
        return get_lan_ip()
    return host


def build_http_info(host: str, port: int, data_path: pathlib.Path) -> Dict[str, Any]:
    visible_host = url_host(host)
    return {
        "host": host,
        "port": port,
        "listen_url": f"http://{host}:{port}/data.json",
        "data_url": f"http://{visible_host}:{port}/data.json",
        "health_url": f"http://{visible_host}:{port}/healthz",
        "data_path": str(data_path),
    }


def atomic_write_json(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


class SharedState:
    def __init__(self, host: str, port: int, data_path: pathlib.Path):
        self.host = host
        self.port = port
        self.data_path = data_path
        self.started_at = utc_now()
        self.http_running = False
        self.http_error = ""
        self.last_payload: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def set_http_status(self, running: bool, error: str = "") -> None:
        with self.lock:
            self.http_running = running
            self.http_error = error

    def set_payload(self, payload: Dict[str, Any]) -> None:
        with self.lock:
            self.last_payload = payload

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "server": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "started_at": self.started_at,
                },
                "http": {
                    **build_http_info(self.host, self.port, self.data_path),
                    "running": self.http_running,
                    "error": self.http_error,
                },
                "latest": self.last_payload,
            }


def generate_data(args: argparse.Namespace, state: SharedState) -> Dict[str, Any]:
    data_path = pathlib.Path(args.dir).expanduser() / "data.json"
    http_info = build_http_info(args.host, args.port, data_path)
    session_scan = scan_session_files()

    try:
        raw = run_codex_status_command(timeout_seconds=int(os.environ.get("CODEX_STATUS_TIMEOUT_SECONDS", "20")))
        usage = parse_codex_status(raw, args.interval)
        payload: Dict[str, Any] = {
            "ok": True,
            "generated_at": utc_now(),
            "source": "codex_status",
            "http": http_info,
            "usage": usage,
            "session_scan": session_scan,
        }
    except Exception as exc:
        log(f"usage generation failed: {exc}")
        payload = {
            "ok": False,
            "generated_at": utc_now(),
            "error": str(exc),
            "source": "codex_status",
            "http": http_info,
            "session_scan": session_scan,
        }

    try:
        atomic_write_json(data_path, payload)
    except Exception as exc:
        log(f"failed to write {data_path}: {exc}")
        payload["ok"] = False
        payload["write_error"] = str(exc)

    state.set_payload(payload)
    return payload


def write_startup_payload(args: argparse.Namespace, state: SharedState) -> None:
    data_path = pathlib.Path(args.dir).expanduser() / "data.json"
    payload = {
        "ok": False,
        "generated_at": utc_now(),
        "status": "starting",
        "source": "startup",
        "http": build_http_info(args.host, args.port, data_path),
        "message": "usage data refresh is running in the background",
    }
    try:
        atomic_write_json(data_path, payload)
    except Exception as exc:
        log(f"failed to write startup payload to {data_path}: {exc}")
        payload["write_error"] = str(exc)
    state.set_payload(payload)


def refresh_loop(args: argparse.Namespace, state: SharedState) -> None:
    interval = max(1, int(args.interval))
    while True:
        try:
            generate_data(args, state)
        except Exception:
            log("unexpected refresh loop error:\n" + traceback.format_exc())
        time.sleep(interval)


class UsageRequestHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *handler_args: Any, directory: pathlib.Path, state: SharedState, token: str, **kwargs: Any):
        self.directory = directory
        self.state = state
        self.token = token
        super().__init__(*handler_args, **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"http {self.client_address[0]} {fmt % args}")

    def send_json(self, status: int, payload: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
        data = json.dumps(payload).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        if not self.token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {self.token}"

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            self.send_json(200, {"ok": True})
            return

        if path == "/data.json":
            if not self.authorized():
                self.send_json(401, {"ok": False, "error": "unauthorized"})
                return

            data_path = self.directory / "data.json"
            try:
                data = data_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for key, value in NO_CACHE_HEADERS.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_json(404, {"ok": False, "error": "data.json not found"}, NO_CACHE_HEADERS)
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)}, NO_CACHE_HEADERS)
            return

        self.send_json(404, {"ok": False, "error": "not found"})


def run_http_server(args: argparse.Namespace, state: SharedState) -> None:
    directory = pathlib.Path(args.dir).expanduser()
    token = os.environ.get("CODEX_USAGE_LAN_TOKEN", "")
    handler = functools.partial(UsageRequestHandler, directory=directory, state=state, token=token)

    try:
        server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        state.set_http_status(False, str(exc))
        log(f"HTTP server failed to start on {args.host}:{args.port}: {exc}")
        return
    except Exception as exc:
        state.set_http_status(False, str(exc))
        log(f"HTTP server startup error: {exc}")
        return

    state.set_http_status(True, "")
    log(f"HTTP server listening on {args.host}:{args.port}, serving {directory}")
    try:
        server.serve_forever(poll_interval=0.5)
    except Exception as exc:
        state.set_http_status(False, str(exc))
        log(f"HTTP server stopped: {exc}")
    finally:
        server.server_close()


def json_rpc_response(request_id: Any, result: Any = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result if result is not None else {}
    return response


def json_rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return json_rpc_response(request_id, error=error)


def write_rpc(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def tool_status_result(state: SharedState) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(state.snapshot(), indent=2, sort_keys=True),
            }
        ],
        "isError": False,
    }


def handle_rpc(request: Dict[str, Any], state: SharedState) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    if request_id is None:
        return None

    if method == "initialize":
        protocol_version = params.get("protocolVersion", "2024-11-05") if isinstance(params, dict) else "2024-11-05"
        return json_rpc_response(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "ping":
        return json_rpc_response(request_id, {})

    if method == "tools/list":
        return json_rpc_response(
            request_id,
            {
                "tools": [
                    {
                        "name": "codex_usage_lan_status",
                        "description": "Show the Codex Usage LAN server status and data.json URL.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ]
            },
        )

    if method == "tools/call":
        if not isinstance(params, dict):
            return json_rpc_error(request_id, -32602, "Invalid params")
        if params.get("name") != "codex_usage_lan_status":
            return json_rpc_error(request_id, -32602, "Unknown tool")
        return json_rpc_response(request_id, tool_status_result(state))

    return json_rpc_error(request_id, -32601, f"Method not found: {method}")


def mcp_loop(state: SharedState) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            write_rpc(json_rpc_error(None, -32700, "Parse error", str(exc)))
            continue

        if not isinstance(request, dict):
            write_rpc(json_rpc_error(None, -32600, "Invalid Request"))
            continue

        try:
            response = handle_rpc(request, state)
            if response is not None:
                write_rpc(response)
        except Exception as exc:
            log("MCP request failed:\n" + traceback.format_exc())
            request_id = request.get("id")
            if request_id is not None:
                write_rpc(json_rpc_error(request_id, -32603, "Internal error", str(exc)))


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex usage LAN MCP server")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds")
    parser.add_argument("--dir", default="~/.codex-usage-lan/public", help="Directory to serve")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    data_path = pathlib.Path(args.dir).expanduser() / "data.json"
    state = SharedState(args.host, args.port, data_path)

    log("starting Codex Usage LAN MCP server")
    write_startup_payload(args, state)

    threading.Thread(target=refresh_loop, args=(args, state), daemon=True, name="codex-usage-refresh").start()
    threading.Thread(target=run_http_server, args=(args, state), daemon=True, name="codex-usage-http").start()

    try:
        mcp_loop(state)
    except KeyboardInterrupt:
        log("shutdown requested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
