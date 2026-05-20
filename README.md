# Codex Usage LAN

Codex Usage LAN is a Codex plugin that starts a bundled MCP server. The MCP server refreshes Codex usage data in the background and exposes `data.json` on the local network for clients such as ESP32.

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
6. A background thread refreshes `data.json` with Codex usage data every 60 seconds.
7. A second background thread serves `/data.json` and `/healthz` over HTTP.

This plugin does not declare hooks and does not require `/hooks` trust.

## Manual Run

From the plugin directory:

```bash
python3 bin/codex_usage_lan_mcp.py --host 0.0.0.0 --port 8000 --interval 60
```

The script is also a stdio MCP server. Logs go to stderr only. stdout is reserved for newline-delimited JSON-RPC.

## Test

In one terminal, run the server:

```bash
python3 bin/codex_usage_lan_mcp.py --host 0.0.0.0 --port 8000 --interval 60
```

In another terminal:

```bash
curl http://127.0.0.1:8000/data.json
curl http://127.0.0.1:8000/healthz
python3 test/test_http_client.py
```

The test client defaults to `http://127.0.0.1:8000/data.json` and also checks `/healthz`.

## Install Into Codex Local Marketplace

One simple local layout is:

```bash
mkdir -p ~/plugins ~/.agents/plugins
cp -R codex-usage-lan-plugin ~/plugins/codex-usage-lan
```

Then add an entry to `~/.agents/plugins/marketplace.json`:

```json
{
  "name": "local",
  "interface": {
    "displayName": "Local"
  },
  "plugins": [
    {
      "name": "codex-usage-lan",
      "source": {
        "source": "local",
        "path": "./plugins/codex-usage-lan"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

## Relative And Absolute MCP Paths

`.mcp.json` uses a plugin-relative script path:

```json
"cwd": ".",
"args": ["./bin/codex_usage_lan_mcp.py", "--host", "0.0.0.0", "--port", "8000", "--interval", "60", "--dir", "~/.codex-usage-lan/public"]
```

The `cwd` entry is important. Without it, Codex may start the MCP process from the current project directory instead of the installed plugin directory, causing `./bin/codex_usage_lan_mcp.py` to fail before the MCP initialize handshake.

If Codex relative path handling is unstable in your environment, change `cwd` and the first arg to absolute paths:

```json
"cwd": "/absolute/path/to/codex-usage-lan-plugin",
"args": ["/absolute/path/to/codex-usage-lan-plugin/bin/codex_usage_lan_mcp.py", "--host", "0.0.0.0", "--port", "8000", "--interval", "60", "--dir", "~/.codex-usage-lan/public"]
```

## Check In Codex

After starting Codex with the plugin installed, run:

```text
/mcp
```

Look for the `codex-usage-lan` MCP server. The server exposes one tool named `codex_usage_lan_status`, which reports the HTTP server status, the `data.json` path, and the latest generated JSON.

## ESP32 URL

Use the computer's LAN IP address:

```text
http://电脑IP:8000/data.json
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
  "generated_at": "2026-05-20T10:00:00Z",
  "source": "codex_status",
  "usage": {
    "five_h_pct": 80,
    "five_h_reset": "1h 23m",
    "weekly_pct": 55,
    "weekly_reset": "2d 4h",
    "model": "gpt-5.3-codex",
    "account": "user@example.com",
    "scraped_at": "2026-05-20T10:00:00Z",
    "sample_interval_seconds": 60
  }
}
```

On failure, the server still writes JSON:

```json
{
  "ok": false,
  "generated_at": "2026-05-20T10:00:00Z",
  "error": "could not parse usage percentages from codex status output"
}
```

## Common Problems

### No usage data

Check whether `codex` is on `PATH` for the process that starts the plugin. You can also set `CODEX_BIN` to an explicit executable path. Run the script manually and inspect stderr logs.

### Port 8000 is occupied

The MCP server keeps running even if the HTTP server cannot bind. Change `.mcp.json` to use another port, for example `--port 8001`, then restart Codex.

### LAN access fails because of firewall

Allow inbound TCP traffic to the selected port on the computer running Codex. Also make sure the ESP32 and computer are on the same LAN.

### Codex session path is different

The server scans `~/.codex` by default. Set `CODEX_USAGE_LAN_SESSION_DIR` if your Codex data lives elsewhere.

### Plugin did not start the MCP server

Run `/mcp` inside Codex and confirm that `codex-usage-lan` is listed. If it is missing, verify the marketplace entry, `.codex-plugin/plugin.json`, and `.mcp.json`.

### stdout pollution breaks MCP

MCP over stdio requires stdout to contain only JSON-RPC messages. This server writes all logs to stderr. If you modify the script, keep print-style logs off stdout.
