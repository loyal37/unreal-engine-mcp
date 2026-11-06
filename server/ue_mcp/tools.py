"""Tool definitions."""
from __future__ import annotations

from . import logs, screenshot
from .app import App
from .editor import helper_hash
from .mcp_stdio import Image, Server, ToolError

VERSION = "0.1.0"

INSTRUCTIONS = """\
Works with the user's RUNNING Unreal Editor through Python remote execution. Never asks the editor
to close, never launches commandlets.
- Nothing is saved until save_assets. Ask the user before saving; edits stay in the editor's
  unsaved state and are undoable with Ctrl+Z (each tool call is one undo step).
- material_parameters tells whether each parameter is visible in the material instance editor:
  only parameters reachable from the material outputs are (unconnected nodes are not).
- Material graph edits refuse a material that is open in the Material Editor (it edits a copy).
- Paths are /Game/... object or package paths.
- When a tool reports an editor-side error, read it before retrying: the editor may be busy."""

STR = {"type": "string"}
BOOL = {"type": "boolean"}
INT = {"type": "integer"}
ANY = {}
STR_LIST = {"type": "array", "items": {"type": "string"}}


def build(app: App) -> Server:
    srv = Server("unreal-editor", VERSION, INSTRUCTIONS)

    # ------------------------------------------------------------------ status, python, logs
    @srv.tool("editor_status", """
Which editor is running: project file, engine version, editor process, Play-In-Editor state,
unsaved packages, open asset editors, which HT script libraries are loaded, and the log file.
Also lists every editor that answers discovery.""")
    def editor_status():
        nodes = app.link.discover()
        listing = [{"project_root": n.project_root, "project_name": n.info.get("project_name"),
                    "engine_version": n.info.get("engine_version"), "node_id": n.node_id} for n in nodes]
        try:
            st = app.call("status")
        except ToolError as e:
            return {"connected": False, "error": str(e), "editors_answering": listing,
                    "remote_execution_enabled_in_project": app.link.settings.enabled}
        st["connected"] = True
        st["log_file"] = str(app.log_path())
        st["editors_answering"] = listing
        st["server"] = {"version": VERSION, "helper": helper_hash()}
        return st

    @srv.tool("run_python", """
Runs Python in the live editor (module `unreal` is imported). Variables persist between calls.
Returns stdout, stderr, the value of a final bare expression, and the editor log lines written
during the call (UE_LOG output, warnings, errors). Pass `transaction` to make the whole run one
Ctrl+Z step (call obj.modify() before changing objects that MaterialEditingLibrary touches).""",
              {"code": STR, "transaction": STR, "timeout": {"type": "number"}}, ["code"], read_only=False)
    def run_python(code: str, transaction: str | None = None, timeout: float | None = None):
        result, lines = app.with_log(lambda: app.call("run_python", {"code": code, "transaction": transaction}, timeout))
        result["log"] = lines
        return result

    @srv.tool("console_command", """
Runs an editor console command (for example `stat unit` or `obj list class=Material`) and returns the
log lines it printed. Commands that would quit or crash the editor are refused.""",
              {"command": STR}, ["command"], read_only=False)
    def console_command(command: str):
        result, lines = app.with_log(lambda: app.call("console_command", {"command": command}))
        result["log"] = lines
        return result

    @srv.tool("output_log", """
Reads the editor's output log from its log file (works even while the editor is busy). Filters:
`contains` (text), `regex`, `categories` (e.g. ["LogPython","LogMaterial"]), `min_verbosity`
(Error, Warning, Display, Log). `since_last_call` returns only lines written since the previous
output_log call.""",
              {"lines": INT, "contains": STR, "regex": STR, "categories": STR_LIST, "min_verbosity": STR,
               "since_last_call": BOOL})
    def output_log(lines: int = 100, contains: str | None = None, regex: str | None = None,
                   categories: list | None = None, min_verbosity: str | None = None, since_last_call: bool = False):
        path = app.log_path()
        offset = app.take_log_offset(path)
        if since_last_call and offset is not None:
            got, _ = logs.read_from(path, offset)
        else:
            got = logs.tail(path, 0 if (contains or regex or categories or min_verbosity) else lines)
        got = [ln for ln in got if "ue-mcp-marker-" not in ln]
        got = logs.filter_lines(got, contains, categories, min_verbosity, regex)
        if lines > 0:
            got = got[-lines:]
        return {"log_file": str(path), "lines": got}

    @srv.tool("screenshot", """
Captures an editor window as an image (from outside the editor; nothing in the editor changes).
`window`: part of a window title (default: the main editor window). `list_windows`: only list the
editor's windows.""",
              {"window": STR, "max_width": INT, "list_windows": BOOL})
    def take_screenshot(window: str | None = None, max_width: int = 1600, list_windows: bool = False):
        pid = app.editor_pid()
        windows = screenshot.list_windows(pid)
        if not windows:
            raise ToolError("the editor has no visible window")
        if list_windows:
            return {"windows": [{k: w[k] for k in ("title", "width", "height", "minimized")} for w in windows]}
        target = windows[0]
        if window:
            matches = [w for w in windows if window.casefold() in w["title"].casefold()]
            if not matches:
                raise ToolError(f"no editor window title contains {window!r}; windows: {[w['title'] for w in windows]}")
            target = matches[0]
        if target["minimized"]:
            raise ToolError(f"window {target['title']!r} is minimized")
        png, w, h, black = screenshot.capture(target["hwnd"], max_width)
        note = {"window": target["title"], "size": [w, h]}
        if black:
            note["warning"] = "the capture is (almost) black"
        return [note, Image(png)]

    # ------------------------------------------------------------------ assets
    @srv.tool("find_assets", """
Searches the asset registry (nothing is loaded). `class_name` is the asset class (Material,
MaterialInstanceConstant, SkeletalMesh, Texture2D, AnimBlueprint, HTToggleGraph, ...); `name` is a
substring or glob.""",
              {"path": STR, "class_name": STR, "name": STR, "recursive": BOOL, "limit": INT})
    def find_assets(path: str = "/Game", class_name: str | None = None, name: str | None = None,
                    recursive: bool = True, limit: int = 200):
        return app.call("find_assets", dict(path=path, class_name=class_name, name=name, recursive=recursive, limit=limit))

    @srv.tool("asset_info", """
Class, package files on disk, unsaved state, dependencies and referencers (asset registry), and
type details: parent chain of material instances, slots/LODs/skeleton of meshes, compression and
sRGB of textures, parent class of Blueprints.""",
              {"path": STR, "dependencies": BOOL, "referencers": BOOL, "hard_only": BOOL}, ["path"])
    def asset_info(path: str, dependencies: bool = True, referencers: bool = True, hard_only: bool = False):
        return app.call("asset_info", dict(path=path, dependencies=dependencies, referencers=referencers,
                                           hard_only=hard_only))

    @srv.tool("open_asset", "Opens an asset in its editor and selects it in the Content Browser, so the user sees it.",
              {"path": STR, "sync_browser": BOOL}, ["path"], read_only=False)
    def open_asset(path: str, sync_browser: bool = True):
        return app.call("open_asset", dict(path=path, sync_browser=sync_browser))

    @srv.tool("unsaved_assets", "Packages with unsaved changes in the editor.")
    def unsaved_assets():
        return app.call("unsaved_assets")

    @srv.tool("save_assets", """
Saves the named assets (only these). Before writing, copies their current files to
<Project>/Saved/Backups/ue-mcp-<date>/ (first copy of the day kept). Ask the user first.""",
              {"paths": STR_LIST, "backup": BOOL}, ["paths"], read_only=False)
    def save_assets(paths: list, backup: bool = True):
        return app.call("save_assets", dict(paths=paths, backup=backup), timeout=300)

    # ------------------------------------------------------------------ materials
    @srv.tool("material_parameters", """
Every parameter of a material or material instance: type, group, sort priority, default, the
instance's value, whether this instance overrides it (or which parent does), the node count, and
`visible_in_instance_editor` (reachable from the material outputs under the instance's static
switch values; unconnected parameter nodes are NOT visible and cannot be overridden in the
instance editor). `only_hidden` lists just the invisible ones.""",
              {"path": STR, "only_hidden": BOOL}, ["path"])
    def material_parameters(path: str, only_hidden: bool = False):
        return app.call("material_parameters", dict(path=path, only_hidden=only_hidden))

    @srv.tool("material_graph", """
Nodes of a material's graph (for an instance: its base material; also works on material functions)
with class, parameter name, group, default, position, input connections (from node + output
name), the material outputs and what feeds them, and which nodes do not reach any output.""",
              {"path": STR, "include_functions": BOOL}, ["path"])
    def material_graph(path: str, include_functions: bool = False):
        return app.call("material_graph", dict(path=path, include_functions=include_functions), timeout=120)

    @srv.tool("material_add_parameter", """
Adds a parameter node to a material and optionally connects it. `type`: scalar, vector, texture,
texture_object, static_switch, static_bool. `connect_to`: a material output name ("EmissiveColor",
"BaseColor", ...) or {"node": "<node id or parameter name>", "input": "<pin>"}; `output` is the new
node's output pin ("" = default, "R", "RGB", ...). Undoable; recompiles; not saved. Reports
whether the parameter is now visible in the instance editor. To add parameters without changing
the material's look, use material_expose_parameters.""",
              {"material": STR, "type": STR, "name": STR, "group": STR, "default": ANY, "sort_priority": INT,
               "position": {"type": "array", "items": INT}, "connect_to": ANY, "output": STR,
               "allow_open_editor": BOOL, "allow_duplicate_name": BOOL},
              ["material", "type", "name"], read_only=False)
    def material_add_parameter(**kw):
        return app.call("material_add_parameter", kw, timeout=120)

    @srv.tool("material_connect", """
Connects an existing node's output to a material output (`to`: "EmissiveColor") or to another
node's input (`to`: {"node": "...", "input": "A"}). Undoable; recompiles; not saved.""",
              {"material": STR, "from_node": STR, "to": ANY, "from_output": STR, "allow_open_editor": BOOL},
              ["material", "from_node", "to"], read_only=False)
    def material_connect(**kw):
        return app.call("material_connect", kw, timeout=120)

    @srv.tool("material_expose_parameters", """
Mod helper for editor stand-in materials: adds scalar/vector/texture parameters (reusing nodes that
already exist by name) and wires them as sum * 0 + <what already fed `output`>, so material
instances can override them by name while the material looks the same. Each parameter:
{"name", "type", "group", "default", "sort_priority"}. Undoable; not saved.""",
              {"material": STR, "parameters": {"type": "array", "items": {"type": "object"}}, "output": STR,
               "group": STR, "allow_open_editor": BOOL}, ["material", "parameters"], read_only=False)
    def material_expose_parameters(**kw):
        return app.call("material_expose_parameters", kw, timeout=180)

    @srv.tool("instance_set_parameters", """
Sets and/or clears overrides on a material instance constant, optionally changing its parent.
`set`: {"Name": value} (scalar: number; vector: [r,g,b] or [r,g,b,a]; texture: asset path or null;
static switch: bool). `clear`: names whose override to remove. `dry_run` shows before-values only.
Returns before/after values. Undoable; not saved.""",
              {"instance": STR, "set": {"type": "object"}, "clear": STR_LIST, "parent": STR, "dry_run": BOOL},
              ["instance"], read_only=False)
    def instance_set_parameters(**kw):
        return app.call("instance_set_parameters", kw, timeout=120)

    @srv.tool("instance_create", """
Creates a material instance constant (`path` like /Game/Characters/.../MI_name) under `parent`,
optionally setting parameters. Asset creation itself is not undoable. Not saved.""",
              {"parent": STR, "path": STR, "set": {"type": "object"}}, ["parent", "path"], read_only=False)
    def instance_create(**kw):
        return app.call("instance_create", kw, timeout=120)

    # ------------------------------------------------------------------ meshes, textures
    @srv.tool("mesh_slots", """
Material slots of a skeletal or static mesh: index (= the HT tools' Material ID), slot name,
material, its parent chain, and the LOD/section pairs that use the slot.""",
              {"path": STR}, ["path"])
    def mesh_slots(path: str):
        return app.call("mesh_slots", dict(path=path))

    @srv.tool("mesh_set_slot_materials", """
Assigns materials to mesh slots: assignments [{"slot": index or slot name, "material": path or null}].
Undoable; not saved.""",
              {"path": STR, "assignments": {"type": "array", "items": {"type": "object"}}, "dry_run": BOOL},
              ["path", "assignments"], read_only=False)
    def mesh_set_slot_materials(**kw):
        return app.call("mesh_set_slot_materials", kw, timeout=120)

    @srv.tool("slots_override", """
For every slot of `mesh` that uses one of `materials` directly: creates a child instance (one per
material, or per slot with per_slot=true; `name` template uses {material}, {slot}, {index}, {mesh};
default folder = the parent's folder), sets `set` on it, and assigns it to those slots. Use
dry_run first. Nothing is saved.""",
              {"mesh": STR, "materials": STR_LIST, "set": {"type": "object"}, "name": STR, "folder": STR,
               "per_slot": BOOL, "dry_run": BOOL}, ["mesh", "materials", "set"], read_only=False)
    def slots_override(**kw):
        return app.call("slots_override", kw, timeout=300)

    @srv.tool("texture_check", """
Checks textures against the NTE rules (diffuse BC7 + sRGB, ID_Tex and LightMap BC7 without sRGB,
normal BC5/TC_NORMALMAP without sRGB). Roles come from the material instance parameter a texture is
bound to (pass `instances`), from `roles` {"path": "diffuse|id|lightmap|normal"}, or from name
suffixes (_d, _id, _m/_lm, _n). `fix` applies the expected settings (undoable; not saved).""",
              {"paths": STR_LIST, "folder": STR, "instances": STR_LIST, "roles": {"type": "object"}, "fix": BOOL},
              read_only=False)
    def texture_check(**kw):
        return app.call("texture_check", kw, timeout=600)

    from . import tools_plugins

    tools_plugins.register(srv, app)
    return srv
