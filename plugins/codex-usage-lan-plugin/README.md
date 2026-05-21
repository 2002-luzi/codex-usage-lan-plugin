# Codex Usage LAN

Codex Usage LAN is a Codex plugin that starts a bundled MCP server. The MCP server refreshes Codex usage data in the background and exposes `data.json` on the local network for clients such as ESP32, so that edge-based devices can conviniently fetch and display the Codex usage data.

## Directory Structure

```text
codex-usage-lan-plugin/
├── .codex-plugin/
│   └── plugin.json
├── .mcp.json
├── bin/
│   └── codex_usage_lan_mcp.py
├── README.md
└── test/
    └── test_http_client.py
```

## How It Works

1. Codex starts the plugin.
2. Codex reads `.codex-plugin/plugin.json`.
3. The plugin points `mcpServers` to `.mcp.json`.
4. Codex starts the bundled MCP server from the plugin directory with `python3 ./bin/codex_usage_lan_mcp.py`.
5. The MCP server immediately writes a startup `~/.codex-usage-lan/public/data.json`.
6. A background thread obtains the Codex OAuth refresh token from `~/.codex/auth.json`,  then obtains the Codex usage data every 60 seconds.
7. A second background thread starts a http server on port 8000 by default to serve `/data.json` and `/healthz`.

## Install
After adding this repo as a marketplace source, install the plugin by running:

```bash
codex plugin add codex-usage-lan@codex-plugins-luzioops
```

Restart Codex to apply the changes.

## Verify Installation

1. Restart Codex.
2. Input `/mcp` in the chat input box to open the MCP server list, you should see `codex-usage-lan` in the list.
3. For Desktop App/ IDE Extension users, you may need to open a certain session to actually start the MCP server.
4. Run `curl http://127.0.0.1:8000/data.json`. You should see the `data.json` content.

## Manual Run

From the plugin directory:

```bash
python3 bin/codex_usage_lan_mcp.py --host 0.0.0.0 --port 8000 --interval 60
```

The script is also a stdio MCP server. Logs go to stderr only. stdout is reserved for newline-delimited JSON-RPC.

## ESP32 URL

Use the computer's LAN IP address:

```text
http://computer_IP:8000/data.json
```

Example:

```text
http://192.168.1.23:8000/data.json
```

## Optional Token

Set `CODEX_USAGE_LAN_TOKEN` before starting Codex or before manually running the server:

```bash
export CODEX_USAGE_LAN_TOKEN='your-secret-token'
python3 bin/codex_usage_lan_mcp.py --host 0.0.0.0 --port 8000 --interval 60
```

When the token is set, `GET /data.json` requires:

```text
Authorization: Bearer your-secret-token
```

`GET /healthz` does not require a token.

With curl:

```bash
curl -H "Authorization: Bearer $CODEX_USAGE_LAN_TOKEN" http://127.0.0.1:8000/data.json
```

The test client reads `CODEX_USAGE_LAN_TOKEN` automatically.

## data.json Shape

Successful generation:

```json
{
  "ok": true,
  "generated_at": "2026-05-20T18:03:36+08:00",
  "source": "codex_oauth_api",
  "usage": {
    "account": "",
    "credits": {
      "balance": "0",
      "has_credits": false,
      "unlimited": false
    },
    "five_h_pct": 33,
    "five_h_used_pct": 67,
    "five_h_reset": "2h 42m",
    "five_h_reset_at": "2026-05-20T20:46:06+08:00",
    "five_h_window_seconds": 18000,
    "plan_type": "plus",
    "rate_limit_reached_type": null,
    "sample_interval_seconds": 60,
    "scraped_at": "2026-05-20T18:03:36+08:00",
    "weekly_pct": 76,
    "weekly_used_pct": 24,
    "weekly_reset": "6d 16h",
    "weekly_reset_at": "2026-05-27T10:28:42+08:00",
    "weekly_window_seconds": 604800
  }
}
```

`account` is blank by default so the LAN endpoint does not broadcast your email address. Set `CODEX_USAGE_LAN_INCLUDE_ACCOUNT=1` before starting Codex if you want to include it.

Immediately after startup, `/data.json` can briefly show a startup placeholder while the background refresh calls the Codex OAuth usage API:

```json
{
  "ok": false,
  "generated_at": "2026-05-20T10:00:00Z",
  "source": "startup",
  "status": "starting",
  "message": "usage data refresh is running in the background"
}
```

On failure, the server still writes JSON:

```json
{
  "ok": false,
  "generated_at": "2026-05-20T18:03:36+08:00",
  "source": "codex_oauth_api",
  "error": "Codex usage API failed with HTTP 401: ..."
}
```

## Common Problems

### No usage data

The server reads OAuth credentials from `~/.codex/auth.json` or `$CODEX_HOME/auth.json`, refreshes the access token when needed, then calls `https://chatgpt.com/backend-api/wham/usage`. Run `codex` and complete login if this file is missing or stale. If the OAuth request fails, the server falls back to the latest `rate_limits` event in `~/.codex/sessions/**/*.jsonl`.

### Port 8000 is occupied

The MCP server keeps running even if the HTTP server cannot bind. Change `.mcp.json` to use another port, for example `--port 8001`, then restart Codex.

### LAN access fails because of firewall

Allow inbound TCP traffic to the selected port on the computer running Codex. Also make sure the ESP32 and computer are on the same LAN.

### Codex session path is different

The server scans `~/.codex` by default. Set `CODEX_USAGE_LAN_SESSION_DIR` if your Codex data lives elsewhere.

### Plugin did not start the MCP server

Run `/mcp` inside Codex and confirm that `codex-usage-lan` is listed. 

For Desktop App/ IDE Extension, you may need to **open a certain session** to actually start the MCP server.