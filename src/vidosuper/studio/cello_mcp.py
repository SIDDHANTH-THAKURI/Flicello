"""Cello's tools for its local voice (local/voice.py): a small MCP server over stdio that Claude Code starts.

Every tool call goes back to the running Flicello (`POST /api/cello/live/tool`, with the voice session's token), which
hands it to the page the person is on, exactly as Gemini Live's tool calls are: the page runs the ones that move the
screen and sends the rest to `/api/cello/act` with the same checks as its own buttons. The answer comes back here as
the tool's result.

    python -m vidosuper.studio.cello_mcp      (FLICELLO_URL, CELLO_LIVE_TOKEN in its environment)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("FLICELLO_URL", "http://127.0.0.1:8765")
TOKEN = os.environ.get("CELLO_LIVE_TOKEN", "")


def tools() -> list[dict]:
    from .cello import TOOLS

    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t.get("parameters") or {"type": "object", "properties": {}}} for t in TOOLS]


def call(name: str, args: dict) -> tuple[str, bool]:
    """The tool run by the page: (its result as text, whether it failed)."""
    req = urllib.request.Request(f"{URL}/api/cello/live/tool", data=json.dumps({"name": name, "args": args}).encode(),
                                 headers={"Content-Type": "application/json", "X-Cello-Live": TOKEN}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=900) as r:  # noqa: S310 - the Flicello on this machine
            out = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return f"Not done: {exc.read().decode('utf-8', 'replace')[:300]}", True
    except (urllib.error.URLError, OSError) as exc:
        return f"Not done: Flicello didn't answer ({exc})", True
    result = out.get("result", out)
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return text, text.startswith("Not done")


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message; the reply (None for a notification)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "cello", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        text, failed = call(str(params.get("name", "")), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": failed}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
