"""Editor-side half of the Unreal Engine MCP server.

The server imports this package inside the running editor's Python (through remote
execution) and calls `handle(request_file, response_file)`. Requests and replies are
JSON files so that only a short bootstrap travels over the remote-execution socket.

`ensure_current(hash)` reloads the package when its source changed, so tool updates
never need an editor restart. State that must survive reloads (the `run_python`
namespace) lives in the separate module `ue_mcp_state`.
"""
from __future__ import annotations

import sys

HELPER_HASH = None


def ensure_current(helper_hash: str):
    """Returns this package, reloaded first when the server's copy changed."""
    mod = sys.modules[__name__]
    if mod.HELPER_HASH == helper_hash:
        return mod
    if mod.HELPER_HASH is not None:
        for name in [n for n in sys.modules if n == __name__ or n.startswith(__name__ + ".")]:
            del sys.modules[name]
        import importlib

        mod = importlib.import_module(__name__)
    mod.HELPER_HASH = helper_hash
    mod._load_modules()
    return mod


def _load_modules():
    # Importing registers every exposed function in core.FUNCS.
    from . import core, status, assets, materials, meshes, textures, plugins  # noqa: F401


def handle(request_path: str, response_path: str) -> None:
    from . import core

    core.handle(request_path, response_path)
