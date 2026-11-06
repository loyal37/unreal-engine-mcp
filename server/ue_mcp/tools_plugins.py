"""Tools backed by the HT plugins' script libraries (phase 2 and 3)."""
from __future__ import annotations

from .app import App
from .mcp_stdio import Server


def register(srv: Server, app: App) -> None:
    @srv.tool("script_libraries", "Lists the functions of the HT script libraries loaded in the editor (None = not loaded).")
    def script_libraries():
        return app.call("script_libraries")
