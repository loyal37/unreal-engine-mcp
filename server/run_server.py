"""Starts the Unreal Editor MCP server (stdio). Point an MCP host at this file:

    python "E:\\unreal engine mcp\\server\\run_server.py" --project D:\\ueproject\\HT
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ue_mcp.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
