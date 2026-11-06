"""Calls into the HT plugins' script libraries (HTBlueprintToggleTool, NTEPanelSystem)."""
from __future__ import annotations

import json

import unreal

from .core import UserError, expose


def lib(name: str):
    lib_cls = getattr(unreal, name, None)
    if lib_cls is None:
        raise UserError(f"unreal.{name} is not loaded: install HT Blueprint Toggle Tool 1.6.4 / NTE Panel System "
                        "0.1.26 or later and restart the editor")
    return lib_cls


def call_json(library: str, function: str, *args):
    fn = getattr(lib(library), function, None)
    if fn is None:
        raise UserError(f"unreal.{library}.{function} is missing: the installed plugin is older than this server")
    raw = fn(*args)
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return {"raw": str(raw)}


@expose
def script_libraries() -> dict:
    out = {}
    for name in ("HTModScriptLibrary", "NTEPanelScriptLibrary"):
        cls = getattr(unreal, name, None)
        out[name] = sorted(n for n in dir(cls) if not n.startswith("_")) if cls else None
    return out
