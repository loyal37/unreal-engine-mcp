"""Editor status, the log marker, run_python and console commands."""
from __future__ import annotations

import ast
import contextlib
import io
import os
import sys
import traceback
import types
import uuid

import unreal

from .core import UserError, dirty_package_names, edited_assets, expose, to_json

# Survives helper reloads: variables defined by run_python stay available.
STATE = sys.modules.setdefault("ue_mcp_state", types.ModuleType("ue_mcp_state"))
if not hasattr(STATE, "namespace"):
    STATE.namespace = {"__name__": "__ue_mcp__", "unreal": unreal}

BLOCKED_CONSOLE = ("quit", "exit", "crash", "debug crash", "debug assert", "debug ensure", "debug gpf",
                   "debug fatal", "debug oom", "debug hang", "debug eatmem", "restartlevel")


def _script_libraries() -> dict:
    return {name: hasattr(unreal, name) for name in ("HTModScriptLibrary", "NTEPanelScriptLibrary")}


@expose
def status() -> dict:
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    pie = None
    try:
        pie = editor.get_game_world()
    except Exception:  # noqa: BLE001
        pass
    project_file = unreal.Paths.get_project_file_path()
    return {
        "project_file": unreal.Paths.convert_relative_path_to_full(project_file) if project_file else None,
        "project_name": unreal.SystemLibrary.get_game_name(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "play_in_editor": pie is not None,
        "unsaved_packages": dirty_package_names(),
        "open_asset_editors": [a.get_path_name() for a in edited_assets() if a is not None],
        "script_libraries": _script_libraries(),
    }


@expose
def log_marker() -> dict:
    marker = "ue-mcp-marker-" + uuid.uuid4().hex
    unreal.log(marker)
    return {
        "marker": marker,
        "project_name": unreal.SystemLibrary.get_game_name(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "project_log_dir": unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_log_dir()),
        "pid": os.getpid(),
    }


@expose
def run_python(code: str, transaction: str | None = None) -> dict:
    """Runs code in a namespace that persists between calls. The value of a final bare
    expression is returned, like an interactive prompt."""
    try:
        tree = ast.parse(code, "<ue-mcp>", "exec")
    except SyntaxError as e:
        raise UserError(f"syntax error: {e}")
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    out, err = io.StringIO(), io.StringIO()
    ns = STATE.namespace
    value = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        ctx = unreal.ScopedEditorTransaction(transaction) if transaction else contextlib.nullcontext()
        try:
            with ctx:
                exec(compile(tree, "<ue-mcp>", "exec"), ns)
                if last is not None:
                    value = eval(compile(last, "<ue-mcp>", "eval"), ns)
        except Exception:  # noqa: BLE001
            raise UserError("the code raised an exception", stdout=out.getvalue(), stderr=err.getvalue(),
                            traceback=traceback.format_exc())
    result = {"stdout": out.getvalue(), "stderr": err.getvalue()}
    if value is not None:
        result["value"] = to_json(value)
        result["repr"] = repr(value)[:4000]
    return result


@expose
def console_command(command: str) -> dict:
    text = command.strip()
    low = text.lower()
    if any(low == b or low.startswith(b + " ") for b in BLOCKED_CONSOLE):
        raise UserError(f"refused: {text!r} would end or break the user's editor session")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, text)
    return {"executed": text}
