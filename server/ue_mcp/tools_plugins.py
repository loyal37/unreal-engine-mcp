"""Tools backed by the HT plugins (phase 2) and the mod workflow helpers (phase 3)."""
from __future__ import annotations

from . import cooked
from .app import App
from .mcp_stdio import Server, ToolError

STR = {"type": "string"}
BOOL = {"type": "boolean"}
OBJ = {"type": "object"}
STR_LIST = {"type": "array", "items": {"type": "string"}}

NEEDS = " Needs HT Blueprint Toggle Tool 1.6.4+ loaded in the editor."
NEEDS_PANEL = " Needs NTE Panel System 0.1.26+ loaded in the editor."


def register(srv: Server, app: App) -> None:
    @srv.tool("script_libraries", "Functions of the HT script libraries loaded in the editor (null = not loaded).")
    def script_libraries():
        return app.call("script_libraries")

    # ------------------------------------------------------------------ toggle trees
    @srv.tool("toggle_graph_describe", """
Reads an HT toggle graph asset (TG_...): target Anim Blueprint / SaveGame / character / tree name,
preview mesh, and every node (part, switch, texture_switch, material_switch, group, generate) with
its properties, input links and where its output goes. Node ids look like "Coat#1A2B3C"."""
              + NEEDS, {"path": STR}, ["path"])
    def toggle_graph_describe(path: str):
        return app.call("toggle_graph_describe", {"path": path})

    @srv.tool("toggle_graph_check", """
What Compile would check, without changing anything: condition model, validation errors with node
ids, the state variables (name, keys, options) it resolves to, and the part slot report (Material
IDs that moved off their slot names).""" + NEEDS, {"path": STR}, ["path"])
    def toggle_graph_check(path: str):
        return app.call("toggle_graph_check", {"path": path})

    @srv.tool("toggle_graph_refresh_slots", "The graph's Refresh button: moves Material IDs back onto their slot names and binds unbound parts (one undo step)." + NEEDS,
              {"path": STR}, ["path"], read_only=False)
    def toggle_graph_refresh_slots(path: str):
        return app.call("toggle_graph_refresh_slots", {"path": path})

    @srv.tool("toggle_graph_edit", """
Edits a toggle graph with a list of operations applied as ONE undo step; if one fails, the whole
batch is undone (unless keep_partial). Operations:
 {"op":"add","kind":"part|switch|texture_switch|material_switch|group","alias":"a","pos":[x,y],"props":{...}}
 {"op":"set","node":"<id|alias|unique comment>","props":{...}}
 {"op":"connect","from":"<node>","to":"<node>","pin":"option N | input N"}  (omit pin for the next free input)
 {"op":"disconnect","from":"<node>","to":"<node>"}   {"op":"remove","node":"<node>"}
 {"op":"set_target","anim_blueprint":..,"savegame_blueprint":..,"character_name":..,"tree_name":..}
props: all: comment, pos, collapsed; part: material_ids, by_material; switches: variable, key,
option_count, initial_option, option_labels; texture_switch: parameter, textures [[per target: per
option path|null]]; material_switch: option_materials [path|null per option]. Parts go on switch
options or into texture/material switch inputs; switches and groups end on the generate node."""
              + NEEDS, {"path": STR, "operations": {"type": "array", "items": OBJ}, "keep_partial": BOOL},
              ["path", "operations"], read_only=False)
    def toggle_graph_edit(path: str, operations: list, keep_partial: bool = False):
        return app.call("toggle_graph_edit", {"path": path, "operations": operations, "keep_partial": keep_partial},
                        timeout=120)

    @srv.tool("toggle_graph_create", "Creates a new toggle graph asset and optionally sets its target Anim Blueprint, SaveGame Blueprint, character name and tree name. Not saved." + NEEDS,
              {"path": STR, "anim_blueprint": STR, "savegame_blueprint": STR, "character_name": STR, "tree_name": STR},
              ["path"], read_only=False)
    def toggle_graph_create(**kw):
        return app.call("toggle_graph_create", kw)

    @srv.tool("toggle_graph_generate", """
The graph's Compile button: generates the "HT Toggle Tree - <name>" region of the Anim Blueprint
(key handlers, AND conditions, SaveGame restore, texture/material effects). save=false (default)
leaves the Blueprints unsaved; save=true backs up their files first. Run toggle_graph_check first.""" + NEEDS,
              {"path": STR, "save": BOOL}, ["path"], read_only=False, destructive=True)
    def toggle_graph_generate(path: str, save: bool = False):
        return app.call("toggle_graph_generate", {"path": path, "save": save}, timeout=300)

    # ------------------------------------------------------------------ classic generators
    @srv.tool("toggle_generate", """
The main panel's Generate button for the classic modes. params: mode (visibility | texture | material
| texture_tuner), anim_blueprint, savegame_blueprint, variable, key, character_folder (default: the
Anim Blueprint's folder; gives the SaveGame slot "<variable> <character>"), save (default false);
visibility: "visibility" like "1+2;3" (semicolons cycle groups, commas add an all-hidden state),
initial_state; texture: parameter, groups [{source_material, slots, textures}]; material: slots,
materials; texture_tuner: slots, source_material, lightmap and/or id_texture.""" + NEEDS,
              {"params": OBJ}, ["params"], read_only=False, destructive=True)
    def toggle_generate(params: dict):
        return app.call("toggle_generate", {"params": params}, timeout=300)

    @srv.tool("material_instance_build", """
The Material Instance tab: rebuilds the parent material's texture parameter nodes and creates a
four-texture instance. params: material, name, base_color, id_texture, lightmap, normal, save
(default false).""" + NEEDS, {"params": OBJ}, ["params"], read_only=False)
    def material_instance_build(params: dict):
        return app.call("material_instance_build", {"params": params}, timeout=300)

    # ------------------------------------------------------------------ panel
    @srv.tool("panel_scan", "NTE Panel System's Scan: the toggle rows (key toggles and toggle tree variables) and RGB tuners found in an Anim Blueprint." + NEEDS_PANEL,
              {"anim_blueprint": STR}, ["anim_blueprint"])
    def panel_scan(anim_blueprint: str):
        return app.call("panel_scan", {"anim_blueprint": anim_blueprint}, timeout=120)

    @srv.tool("panel_generate", """
NTE Panel System's Generate: builds the in-game panel widget and wires it into the Anim Blueprint.
params: anim_blueprint, open_key (F10), title, patreon_url, output_path (/Game/NTEPanel),
widget_name (WBP_NTEPanel), save (default false; true backs up the Anim Blueprint first), rows /
tuners: [{"variable"|"name", "display_name", "enabled"}] to rename or leave out scanned entries.""" + NEEDS_PANEL,
              {"params": OBJ}, ["params"], read_only=False, destructive=True)
    def panel_generate(params: dict):
        return app.call("panel_generate", {"params": params}, timeout=300)

    # ------------------------------------------------------------------ slots, cooked export
    @srv.tool("slot_match_names", """
The Slot Materials tab's Match Names: puts every material/instance under `folder` whose asset name
equals a slot name (case-insensitive) into that slot of `mesh` (or of the Anim Blueprint's preview
mesh). Undoable; not saved. Lists the slots that matched nothing.""",
              {"folder": STR, "mesh": STR, "anim_blueprint": STR, "dry_run": BOOL}, ["folder"], read_only=False)
    def slot_match_names(**kw):
        return app.call("slot_match_names", kw, timeout=120)

    @srv.tool("cooked_export", """
The Cooked Asset Exporter, without the editor: copies the cooked files (.uasset/.uexp/.ubulk/.uptnl)
of the selected assets from the cooked source folder to <output>/<character folder>, keeping paths.
Defaults come from the exporter window's saved settings: source folder, output folder and its saved
selection for that source. `assets`: relative paths ("ter/x.uasset") or /Game paths. dry_run
(the default) lists files, missing cooks and cooked files older than the saved asset (stale). `package` launches the
packager afterwards (default exe: the one the plugin launches). Cooking itself is not done here.""",
              {"source": STR, "output": STR, "assets": STR_LIST, "overwrite": BOOL, "dry_run": BOOL,
               "package": BOOL, "packager": STR}, read_only=False)
    def cooked_export(source: str | None = None, output: str | None = None, assets: list | None = None,
                      overwrite: bool = False, dry_run: bool = True, package: bool = False,
                      packager: str | None = None):
        try:
            p = cooked.plan(app.project_dir, source, output, assets)
        except ValueError as e:
            raise ToolError(str(e))
        summary = {k: p[k] for k in ("source", "output_root", "game_path", "selection", "missing", "stale")}
        summary["assets"] = [{"asset": r["asset"], "files": len(r["files"])} for r in p["assets"]]
        if dry_run:
            summary["dry_run"] = True
            return summary
        if not p["assets"]:
            raise ToolError("nothing selected")
        summary.update(cooked.export(p, overwrite))
        if package and not summary["failed"]:
            try:
                summary["packager_pid"] = cooked.launch_packager(packager or cooked.DEFAULT_PACKAGER)
            except ValueError as e:
                summary["packager_error"] = str(e)
        return summary
