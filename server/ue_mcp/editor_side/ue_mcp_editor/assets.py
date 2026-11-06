"""Asset search, information, references, saving and opening."""
from __future__ import annotations

import datetime
import fnmatch
import os
import shutil

import unreal

from .core import (UserError, asset_registry, class_name, dirty_package_names, enum_name, expose,
                   is_open_in_editor, load, object_path, package_files, package_name, project_saved_dir)


def _asset_data(path: str):
    ad = asset_registry().get_asset_by_object_path(object_path(path))
    if ad is None or not ad.is_valid():
        raise UserError(f"asset not found: {object_path(path)}")
    return ad


def _ad_object_path(ad) -> str:
    return f"{ad.package_name}.{ad.asset_name}"


def _ad_class(ad) -> str:
    try:
        return str(ad.asset_class_path.asset_name)
    except AttributeError:
        return str(ad.asset_class)


@expose
def find_assets(path: str = "/Game", class_name: str | None = None, name: str | None = None,
                recursive: bool = True, limit: int = 200) -> dict:
    data = asset_registry().get_assets_by_path(path.rstrip("/") or "/Game", recursive=recursive)
    want_cls = class_name.casefold() if class_name else None
    pattern = name.casefold() if name else None
    out = []
    total = 0
    for ad in data:
        cls = _ad_class(ad)
        if want_cls and cls.casefold() != want_cls:
            continue
        if pattern and not fnmatch.fnmatch(str(ad.asset_name).casefold(), pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"):
            continue
        total += 1
        if len(out) < limit:
            out.append({"path": _ad_object_path(ad), "class": cls})
    out.sort(key=lambda a: a["path"].casefold())
    return {"count": total, "truncated": total > len(out), "assets": out}


def _dep_options(hard_only: bool):
    o = unreal.AssetRegistryDependencyOptions()
    o.set_editor_property("include_hard_package_references", True)
    o.set_editor_property("include_soft_package_references", not hard_only)
    o.set_editor_property("include_searchable_names", False)
    o.set_editor_property("include_soft_management_references", False)
    o.set_editor_property("include_hard_management_references", False)
    return o


def _names(values) -> list[str]:
    return sorted({str(v) for v in (values or [])}, key=str.casefold)


def parent_chain(asset) -> list[dict]:
    chain = []
    cur = asset
    seen = set()
    while isinstance(cur, unreal.MaterialInstance) and cur.get_path_name() not in seen:
        seen.add(cur.get_path_name())
        parent = cur.get_editor_property("parent")
        if parent is None:
            chain.append({"path": None, "class": None})
            break
        chain.append({"path": parent.get_path_name(), "class": class_name(parent)})
        cur = parent
    return chain


def _details(asset) -> dict:
    d: dict = {}
    if isinstance(asset, unreal.MaterialInstance):
        d["parent_chain"] = parent_chain(asset)
        base = asset.get_base_material()
        d["base_material"] = base.get_path_name() if base else None
    elif isinstance(asset, unreal.Material):
        for prop in ("material_domain", "blend_mode", "two_sided", "used_with_skeletal_mesh"):
            try:
                v = asset.get_editor_property(prop)
                d[prop] = enum_name(v) if isinstance(v, unreal.EnumBase) else v
            except Exception:  # noqa: BLE001
                pass
        d["expression_count"] = unreal.MaterialEditingLibrary.get_num_material_expressions(asset)
    elif isinstance(asset, unreal.SkeletalMesh):
        mats = asset.get_editor_property("materials")
        d["material_slots"] = len(mats)
        sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
        d["lods"] = sub.get_lod_count(asset)
        skel = asset.get_editor_property("skeleton")
        d["skeleton"] = skel.get_path_name() if skel else None
        phys = asset.get_editor_property("physics_asset")
        d["physics_asset"] = phys.get_path_name() if phys else None
    elif isinstance(asset, unreal.StaticMesh):
        d["material_slots"] = len(asset.get_editor_property("static_materials"))
    elif isinstance(asset, unreal.Texture2D):
        d["size"] = [asset.blueprint_get_size_x(), asset.blueprint_get_size_y()]
        d["compression"] = enum_name(asset.get_editor_property("compression_settings"))
        d["srgb"] = asset.get_editor_property("srgb")
        d["lod_group"] = enum_name(asset.get_editor_property("lod_group"))
    elif isinstance(asset, unreal.Blueprint):
        try:
            gen = asset.generated_class()
            d["generated_class"] = gen.get_path_name() if gen else None
        except Exception:  # noqa: BLE001
            pass
        try:
            parent = asset.get_editor_property("parent_class")
            d["parent_class"] = parent.get_path_name() if parent else None
        except Exception:  # noqa: BLE001
            pass
        if isinstance(asset, unreal.AnimBlueprint):
            try:
                skel = asset.get_editor_property("target_skeleton")
                d["target_skeleton"] = skel.get_path_name() if skel else None
            except Exception:  # noqa: BLE001
                pass
    return d


@expose
def asset_info(path: str, dependencies: bool = True, referencers: bool = True, hard_only: bool = False,
               details: bool = True) -> dict:
    ad = _asset_data(path)
    pkg = str(ad.package_name)
    out = {
        "path": _ad_object_path(ad),
        "class": _ad_class(ad),
        "package": pkg,
        "unsaved": pkg in dirty_package_names(),
        "files": [],
    }
    for f in package_files(pkg):
        st = os.stat(f)
        out["files"].append({"file": f, "bytes": st.st_size,
                             "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    reg = asset_registry()
    if dependencies:
        out["dependencies"] = _names(reg.get_dependencies(pkg, _dep_options(hard_only)))
    if referencers:
        out["referencers"] = _names(reg.get_referencers(pkg, _dep_options(hard_only)))
    if details:
        asset = load(out["path"])
        out["open_in_editor"] = is_open_in_editor(asset)
        out.update(_details(asset))
    return out


@expose
def unsaved_assets() -> dict:
    return {"packages": dirty_package_names()}


@expose
def save_assets(paths: list[str], backup: bool = True) -> dict:
    if not paths:
        raise UserError("name the assets to save")
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    backup_root = os.path.join(project_saved_dir(), "Backups", f"ue-mcp-{stamp}")
    saved, backups, failed = [], [], []
    for p in paths:
        op = object_path(p)
        pkg = package_name(op)
        if not unreal.EditorAssetLibrary.does_asset_exist(op):
            failed.append({"path": op, "error": "asset not found"})
            continue
        if backup:
            for f in package_files(pkg):
                rel = pkg[len("/Game/"):].replace("/", os.sep)
                dest_dir = os.path.join(backup_root, os.path.dirname(rel))
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, os.path.basename(f))
                if not os.path.exists(dest):  # keep the first (oldest) copy of the day
                    shutil.copy2(f, dest)
                    backups.append(dest)
        if unreal.EditorAssetLibrary.save_asset(op, only_if_is_dirty=False):
            saved.append({"path": op, "files": package_files(pkg)})
        else:
            failed.append({"path": op, "error": "save_asset returned false (see the output log)"})
    return {"saved": saved, "failed": failed, "backups": backups, "backup_dir": backup_root if backups else None}


@expose
def open_asset(path: str, sync_browser: bool = True) -> dict:
    asset = load(path)
    unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([asset])
    if sync_browser:
        unreal.EditorAssetLibrary.sync_browser_to_objects([asset.get_path_name()])
    return {"opened": asset.get_path_name()}
