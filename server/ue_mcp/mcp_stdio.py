"""Minimal Model Context Protocol server over stdio (JSON-RPC 2.0, one message per line).

Only the `tools` capability. Standard library only.
"""
from __future__ import annotations

import base64
import json
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]


class ToolError(Exception):
    """A problem the agent can act on; reported with isError=true."""


@dataclass
class Image:
    data: bytes
    mime: str = "image/png"


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[..., Any]
    read_only: bool
    destructive: bool


@dataclass
class Server:
    name: str
    version: str
    instructions: str = ""
    tools: dict[str, Tool] = field(default_factory=dict)

    def tool(self, name: str, description: str, properties: dict | None = None, required: list[str] | None = None,
             read_only: bool = True, destructive: bool = False):
        def deco(fn):
            schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
            if required:
                schema["required"] = required
            self.tools[name] = Tool(name, description.strip(), schema, fn, read_only, destructive)
            return fn

        return deco

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        rid = msg.get("id")
        if method is None:
            return None

        def ok(result: dict) -> dict:
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def err(code: int, message: str) -> dict:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

        if method == "initialize":
            wanted = (msg.get("params") or {}).get("protocolVersion")
            result = {"protocolVersion": wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": self.name, "version": self.version}}
            if self.instructions:
                result["instructions"] = self.instructions
            return ok(result)
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": [{
                "name": t.name, "description": t.description, "inputSchema": t.schema,
                "annotations": {"readOnlyHint": t.read_only, "destructiveHint": t.destructive,
                                "idempotentHint": t.read_only, "openWorldHint": False},
            } for t in self.tools.values()]})
        if method == "tools/call":
            params = msg.get("params") or {}
            tool = self.tools.get(params.get("name", ""))
            if tool is None:
                return err(-32602, f"unknown tool: {params.get('name')}")
            args = params.get("arguments") or {}
            unknown = sorted(set(args) - set(tool.schema["properties"]))
            if unknown:
                return ok({"content": [{"type": "text", "text":
                           f"{tool.name}: unknown argument(s) {unknown}; allowed: {sorted(tool.schema['properties'])}"}],
                           "isError": True})
            try:
                return ok({"content": to_content(tool.fn(**args)), "isError": False})
            except ToolError as e:
                return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
            except TypeError as e:
                return ok({"content": [{"type": "text", "text": f"bad arguments for {tool.name}: {e}"}], "isError": True})
            except Exception as e:  # noqa: BLE001 - a bug on our side: report it, keep serving
                traceback.print_exc(file=sys.stderr)
                return ok({"content": [{"type": "text", "text": f"internal error in {tool.name}: {type(e).__name__}: {e}"}],
                           "isError": True})
        if "id" not in msg:
            return None  # notifications
        return err(-32601, f"method not found: {method}")

    def serve(self) -> None:
        stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
        while True:
            line = stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                stdout.write(json.dumps(reply).encode("utf-8") + b"\n")
                stdout.flush()
                continue
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (self.handle(m) for m in batch if isinstance(m, dict)) if r is not None]
            if not replies:
                continue
            payload = replies if isinstance(msg, list) else replies[0]
            stdout.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


def to_content(out: Any) -> list[dict]:
    items = out if isinstance(out, (list, tuple)) else [out]
    content: list[dict] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, Image):
            content.append({"type": "image", "data": base64.b64encode(item.data).decode("ascii"), "mimeType": item.mime})
        elif isinstance(item, str):
            content.append({"type": "text", "text": item})
        else:
            content.append({"type": "text", "text": json.dumps(item, ensure_ascii=False, indent=1)})
    return content or [{"type": "text", "text": "(no output)"}]
