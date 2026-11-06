"""Skeletal and static mesh material slots."""
from __future__ import annotations

import unreal

from .core import UserError, class_name, expose, load, modify, transaction
from .assets import parent_chain


def _slots(mesh) -> list:
    if isinstance(mesh, unreal.SkeletalMesh):
        return list(mesh.get_editor_property("materials"))
    if isinstance(mesh, unreal.StaticMesh):
        return list(mesh.get_editor_property("static_materials"))
    raise UserError(f"{mesh.get_path_name()} is a {class_name(mesh)}, expected a SkeletalMesh or StaticMesh")


def _sections(mesh) -> dict[int, list[list[int]]]:
    """Material slot index -> [[lod, section], ...]."""
    out: dict[int, list[list[int]]] = {}
    try:
        if isinstance(mesh, unreal.SkeletalMesh):
            sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
            for lod in range(sub.get_lod_count(mesh)):
                for s in range(sub.get_num_sections(mesh, lod)):
                    out.setdefault(sub.get_lod_material_slot(mesh, lod, s), []).append([lod, s])
        else:
            sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
            for lod in range(sub.get_lod_count(mesh)):
                for s in range(mesh.get_num_sections(lod)):
                    out.setdefault(sub.get_lod_material_slot(mesh, lod, s), []).append([lod, s])
    except Exception:  # noqa: BLE001
        pass
    return out


def _slot_row(index: int, slot, sections) -> dict:
    mat = slot.get_editor_property("material_interface")
    row = {
        "index": index,
        "slot_name": str(slot.get_editor_property("material_slot_name")),
        "material": mat.get_path_name() if mat else None,
        "material_class": class_name(mat) if mat else None,
    }
    try:
        row["imported_slot_name"] = str(slot.get_editor_property("imported_material_slot_name"))
    except Exception:  # noqa: BLE001
        pass
    if isinstance(mat, unreal.MaterialInstance):
        row["parents"] = [p["path"] for p in parent_chain(mat)]
    row["sections"] = sections.get(index, [])
    return row


@expose
def mesh_slots(path: str) -> dict:
    mesh = load(path)
    slots = _slots(mesh)
    sections = _sections(mesh)
    rows = [_slot_row(i, s, sections) for i, s in enumerate(slots)]
    by_material: dict[str, list[int]] = {}
    for r in rows:
        by_material.setdefault(r["material"] or "(none)", []).append(r["index"])
    return {"mesh": mesh.get_path_name(), "class": class_name(mesh), "slot_count": len(rows), "slots": rows,
            "slots_by_material": by_material}


def _resolve_slot(slots, key) -> int:
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        i = int(key)
        if not 0 <= i < len(slots):
            raise UserError(f"slot index {i} out of range (0..{len(slots) - 1})")
        return i
    for i, s in enumerate(slots):
        if str(s.get_editor_property("material_slot_name")) == key:
            return i
    names = [str(s.get_editor_property("material_slot_name")) for s in slots]
    raise UserError(f"no slot named {key!r}; slots: {names}")


@expose
def mesh_set_slot_materials(path: str, assignments: list[dict], dry_run: bool = False) -> dict:
    mesh = load(path)
    slots = _slots(mesh)
    plan = []
    for a in assignments:
        i = _resolve_slot(slots, a.get("slot"))
        new = load(a["material"], unreal.MaterialInterface) if a.get("material") else None
        old = slots[i].get_editor_property("material_interface")
        plan.append({"index": i, "slot_name": str(slots[i].get_editor_property("material_slot_name")),
                     "from": old.get_path_name() if old else None, "to": new.get_path_name() if new else None,
                     "_new": new})
    if not dry_run:
        prop = "materials" if isinstance(mesh, unreal.SkeletalMesh) else "static_materials"
        with transaction(f"ue-mcp: set {len(plan)} slot materials on {mesh.get_name()}"):
            modify(mesh)
            for p in plan:
                slot = slots[p["index"]]
                slot.set_editor_property("material_interface", p["_new"])
                slots[p["index"]] = slot
            mesh.set_editor_property(prop, slots)
    for p in plan:
        p.pop("_new")
    return {"mesh": mesh.get_path_name(), "changes": plan, "dry_run": dry_run, "saved": False}


@expose
def slots_override(mesh: str, materials: list[str], set: dict, name: str = "{material}_override",
                   folder: str | None = None, per_slot: bool = False, dry_run: bool = False) -> dict:
    """For every slot that uses one of `materials` directly: create a child instance (one per
    material, or one per slot), set `set` on it, and put it in the slot."""
    from .materials import instance_create, instance_set_parameters

    mesh_asset = load(mesh)
    slots = _slots(mesh_asset)
    targets = {load(m, unreal.MaterialInterface).get_path_name() for m in materials}
    groups: dict[str, list[int]] = {}
    for i, s in enumerate(slots):
        m = s.get_editor_property("material_interface")
        if m is not None and m.get_path_name() in targets:
            key = f"{m.get_path_name()}#{i}" if per_slot else m.get_path_name()
            groups.setdefault(key, []).append(i)
    if not groups:
        raise UserError("no slot uses those materials directly")
    plan = []
    for key, idx in groups.items():
        mat_path = key.split("#", 1)[0]
        mat_name = mat_path.rsplit(".", 1)[-1]
        slot_name = str(slots[idx[0]].get_editor_property("material_slot_name"))
        new_name = name.format(material=mat_name, slot=slot_name, index=idx[0], mesh=mesh_asset.get_name())
        dest = (folder.rstrip("/") if folder else mat_path.rsplit("/", 1)[0]) + "/" + new_name
        plan.append({"parent": mat_path, "slots": idx, "instance": dest,
                     "exists": unreal.EditorAssetLibrary.does_asset_exist(dest)})
    if dry_run:
        return {"mesh": mesh_asset.get_path_name(), "plan": plan, "set": set, "dry_run": True}
    assignments = []
    for p in plan:
        if p["exists"]:
            mi = load(p["instance"], unreal.MaterialInstanceConstant)
            parent = mi.get_editor_property("parent")
            if parent is None or parent.get_path_name() != p["parent"]:
                raise UserError(f"{p['instance']} exists with another parent; pick another name")
            p["parameters"] = instance_set_parameters(mi.get_path_name(), set=set)
        else:
            p["created"] = instance_create(p["parent"], p["instance"], set=set)
        assignments += [{"slot": i, "material": p["instance"]} for i in p["slots"]]
    p_assign = mesh_set_slot_materials(mesh_asset.get_path_name(), assignments)
    return {"mesh": mesh_asset.get_path_name(), "plan": plan, "assigned": p_assign["changes"], "saved": False,
            "note": "save the new instances and the mesh with save_assets when the user agrees"}
