"""Calls into the HT plugins' script libraries (HTBlueprintToggleTool 1.6.4+, NTEPanelSystem 0.1.26+),
and the slot-name matching of the main tool's Slot Materials tab (ported, no C++ needed)."""
from __future__ import annotations

import json
import os
import shutil
import datetime

import unreal

from .core import (UserError, asset_registry, class_name, expose, load, object_path, package_files, package_name,
                   project_saved_dir)

HT = "HTModScriptLibrary"
PANEL = "NTEPanelScriptLibrary"


def lib(name: str):
    lib_cls = getattr(unreal, name, None)
    if lib_cls is None:
        raise UserError(f"unreal.{name} is not loaded: install HT Blueprint Toggle Tool 1.6.4 and NTE Panel System "
                        "0.1.26 (or later) and restart the editor")
    return lib_cls


def call_json(library: str, function: str, *args):
    fn = getattr(lib(library), function, None)
    if fn is None:
        raise UserError(f"unreal.{library}.{function} is missing: the installed plugin is older than this server")
    raw = fn(*args)
    try:
        result = json.loads(str(raw))
    except json.JSONDecodeError:
        raise UserError(f"{function} returned something that is not JSON: {str(raw)[:300]}")
    return result


def checked(result: dict) -> dict:
    """Raises with the plugin's own messages when it reports failure."""
    if not result.get("success"):
        msgs = [e.get("message", "") + (f" (nodes: {', '.join(e['nodes'])})" if e.get("nodes") else "")
                for e in result.get("errors", [])]
        raise UserError("; ".join(m for m in msgs if m) or "the plugin reported a failure", result=result)
    return result


def _backup(paths: list[str]) -> list[str]:
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    root = os.path.join(project_saved_dir(), "Backups", f"ue-mcp-{stamp}")
    out = []
    for p in paths:
        if not p:
            continue
        pkg = package_name(p)
        for f in package_files(pkg):
            rel = pkg[len("/Game/"):].replace("/", os.sep)
            dest_dir = os.path.join(root, os.path.dirname(rel))
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(f))
            if not os.path.exists(dest):
                shutil.copy2(f, dest)
                out.append(dest)
    return out


@expose
def script_libraries() -> dict:
    out = {}
    for name in (HT, PANEL):
        cls = getattr(unreal, name, None)
        out[name] = sorted(n for n in dir(cls) if not n.startswith("_") and n not in dir(unreal.Object)) if cls else None
    return out


# ---------------------------------------------------------------------------- toggle trees
@expose
def toggle_graph_describe(path: str) -> dict:
    return checked(call_json(HT, "describe_toggle_graph", object_path(path)))


@expose
def toggle_graph_check(path: str) -> dict:
    return call_json(HT, "check_toggle_graph", object_path(path))


@expose
def toggle_graph_refresh_slots(path: str) -> dict:
    return call_json(HT, "refresh_toggle_graph_slots", object_path(path))


@expose
def toggle_graph_edit(path: str, operations: list, keep_partial: bool = False) -> dict:
    payload = json.dumps({"operations": operations, "keep_partial": keep_partial}, ensure_ascii=False)
    return call_json(HT, "edit_toggle_graph", object_path(path), payload)


@expose
def toggle_graph_generate(path: str, save: bool = False) -> dict:
    backups = []
    if save:
        desc = toggle_graph_describe(path)
        backups = _backup([desc.get("anim_blueprint"), desc.get("savegame_blueprint")])
    result = call_json(HT, "generate_toggle_graph", object_path(path), bool(save))
    result["backups"] = backups
    return result


@expose
def toggle_graph_create(path: str, anim_blueprint: str | None = None, savegame_blueprint: str | None = None,
                        character_name: str | None = None, tree_name: str | None = None) -> dict:
    lib(HT)
    op = path.strip().split(".", 1)[0]
    folder, name = op.rsplit("/", 1)
    if unreal.EditorAssetLibrary.does_asset_exist(op):
        raise UserError(f"{op} already exists")
    factory_cls = getattr(unreal, "HTToggleGraphFactory", None)
    if factory_cls is None:
        raise UserError("unreal.HTToggleGraphFactory is not available")
    asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, getattr(unreal, "HTToggleGraph", None),
                                                                    factory_cls())
    if asset is None:
        raise UserError(f"could not create {op}")
    target = {k: v for k, v in {"anim_blueprint": anim_blueprint, "savegame_blueprint": savegame_blueprint,
                                "character_name": character_name, "tree_name": tree_name}.items() if v is not None}
    result = {"created": asset.get_path_name(), "saved": False}
    if target:
        result["target"] = toggle_graph_edit(asset.get_path_name(), [{"op": "set_target", **target}])
    return result


# ---------------------------------------------------------------------------- generators
@expose
def toggle_generate(params: dict) -> dict:
    backups = []
    if params.get("save"):
        backups = _backup([params.get("anim_blueprint"), params.get("savegame_blueprint")])
    result = call_json(HT, "generate_toggle", json.dumps(params, ensure_ascii=False))
    result["backups"] = backups
    return result


@expose
def material_instance_build(params: dict) -> dict:
    return call_json(HT, "build_material_instance", json.dumps(params, ensure_ascii=False))


@expose
def panel_scan(anim_blueprint: str) -> dict:
    return call_json(PANEL, "scan_panel", object_path(anim_blueprint))


@expose
def panel_generate(params: dict) -> dict:
    backups = []
    if params.get("save"):
        backups = _backup([params.get("anim_blueprint")])
    result = call_json(PANEL, "generate_panel", json.dumps(params, ensure_ascii=False))
    result["backups"] = backups
    return result


# ---------------------------------------------------------------------------- slot name matching
def _preview_mesh(anim_bp):
    for getter in ("get_preview_mesh",):
        fn = getattr(anim_bp, getter, None)
        if fn is not None:
            try:
                mesh = fn()
                if mesh is not None:
                    return mesh
            except Exception:  # noqa: BLE001
                pass
    for prop in ("preview_skeletal_mesh",):
        try:
            mesh = anim_bp.get_editor_property(prop)
            if mesh is not None:
                return mesh.load_synchronous() if hasattr(mesh, "load_synchronous") else mesh
        except Exception:  # noqa: BLE001
            pass
    skel = anim_bp.get_editor_property("target_skeleton")
    if skel is not None and hasattr(skel, "get_preview_mesh"):
        return skel.get_preview_mesh()
    return None


@expose
def slot_match_names(folder: str, mesh: str | None = None, anim_blueprint: str | None = None,
                     dry_run: bool = False) -> dict:
    """The Slot Materials tab's "Match Names": every material or instance under `folder` whose asset
    name equals a slot name (case-insensitive) goes into that slot."""
    from .meshes import _slots, mesh_set_slot_materials

    if mesh:
        target = load(mesh)
    elif anim_blueprint:
        target = _preview_mesh(load(anim_blueprint, unreal.AnimBlueprint))
        if target is None:
            raise UserError("the Anim Blueprint has no preview Skeletal Mesh; pass mesh instead")
    else:
        raise UserError("pass mesh or anim_blueprint")
    by_name = {}
    for ad in asset_registry().get_assets_by_path(folder.rstrip("/"), recursive=True):
        obj = unreal.load_asset(f"{ad.package_name}.{ad.asset_name}")
        if isinstance(obj, unreal.MaterialInterface):
            by_name[obj.get_name().lower()] = obj.get_path_name()
    if not by_name:
        raise UserError(f"no Material or Material Instance assets under {folder}")
    assignments, unmatched = [], []
    for i, s in enumerate(_slots(target)):
        name = str(s.get_editor_property("material_slot_name"))
        if name in ("", "None"):
            name = str(s.get_editor_property("imported_material_slot_name"))
        hit = by_name.get(name.lower())
        if hit:
            assignments.append({"slot": i, "material": hit})
        else:
            unmatched.append({"index": i, "slot_name": name})
    if not assignments:
        raise UserError("no exact slot-name/material-name matches", unmatched=unmatched)
    result = mesh_set_slot_materials(target.get_path_name(), assignments, dry_run=dry_run)
    result["unmatched_slots"] = unmatched
    return result
