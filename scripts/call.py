"""Developer helper: call one MCP tool without an MCP host.

    python scripts/call.py --project D:\\ueproject\\HT editor_status
    python scripts/call.py --project D:\\ueproject\\HT material_parameters "{\\"path\\": \\"/Game/...\\"}"

Images are written to the folder given with --out (default: the current folder).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from ue_mcp.app import App  # noqa: E402
from ue_mcp.mcp_stdio import to_content  # noqa: E402
from ue_mcp.tools import build  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--out", default=".")
    p.add_argument("--list", action="store_true", help="list tools")
    p.add_argument("tool", nargs="?")
    p.add_argument("args", nargs="?", default="{}")
    a = p.parse_args()
    srv = build(App(a.project))
    if a.list or not a.tool:
        for t in srv.tools.values():
            print(f"{t.name}: {t.description.splitlines()[0]}")
        return 0
    args = json.loads(a.args)
    t0 = time.time()
    reply = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": a.tool, "arguments": args}})
    res = reply["result"]
    for i, item in enumerate(res["content"]):
        if item["type"] == "image":
            path = Path(a.out) / f"{a.tool}-{i}.png"
            path.write_bytes(base64.b64decode(item["data"]))
            print(f"[image written to {path}]")
        else:
            print(item["text"])
    print(f"--- isError={res['isError']} in {time.time() - t0:.2f} s", file=sys.stderr)
    return 1 if res["isError"] else 0


if __name__ == "__main__":
    sys.exit(main())
