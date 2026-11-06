"""Entry point: python -m ue_mcp --project D:\\ueproject\\HT"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ue_mcp", description="MCP server for a running Unreal Editor")
    parser.add_argument("--project", default=os.environ.get("UE_MCP_PROJECT"),
                        help="project folder whose editor to talk to (or set UE_MCP_PROJECT)")
    parser.add_argument("--timeout", type=float, default=60.0, help="default seconds per editor call")
    args = parser.parse_args(argv)
    if not args.project:
        parser.error("--project is required")
    project = Path(args.project)
    if project.suffix.lower() == ".uproject":
        project = project.parent
    if not project.is_dir():
        parser.error(f"project folder not found: {project}")

    from .app import App
    from .tools import build

    build(App(project, timeout=args.timeout)).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
