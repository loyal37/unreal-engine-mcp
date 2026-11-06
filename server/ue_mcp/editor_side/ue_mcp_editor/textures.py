"""Texture settings checks for NTE mods.

The game expects: diffuse BC7 + sRGB, ID_Tex and LightMap BC7 without sRGB, normal maps
BC5 (TC_NORMALMAP) without sRGB.
"""
from __future__ import annotations

import re

import unreal

from .core import UserError, enum_name, expose, load, modify, transaction

RULES = {
    "diffuse": ("TC_BC7", True),
    "id": ("TC_BC7", False),
    "lightmap": ("TC_BC7", False),
    "normal": ("TC_NORMALMAP", False),
}

PARAM_ROLES = [
    (re.compile(r"^(basecolor|base_color|diffuse|albedo|d)$|basecolor|diffuse|albedo", re.I), "diffuse"),
    (re.compile(r"id[_ ]?tex|^id$", re.I), "id"),
    (re.compile(r"light[_ ]?map|^ilm$|^lm$", re.I), "lightmap"),
    (re.compile(r"normal", re.I), "normal"),
]
NAME_ROLES = [
    (re.compile(r"(_d|_bc|_basecolor|_diffuse|_albedo)$", re.I), "diffuse"),
    (re.compile(r"(_id|_idtex|_id_tex)$", re.I), "id"),
    (re.compile(r"(_lm|_ilm|_lightmap|_m)$", re.I), "lightmap"),
    (re.compile(r"(_n|_nm|_normal|_normalmap)$", re.I), "normal"),
]


def _role_from_param(name: str):
    for rx, role in PARAM_ROLES:
        if rx.search(name):
            return role
    return None


def _role_from_name(name: str):
    for rx, role in NAME_ROLES:
        if rx.search(name):
            return role
    return None


def _collect(paths, folder, instances) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for p in paths or []:
        t = load(p, unreal.Texture)
        found[t.get_path_name()] = {"texture": t, "role": _role_from_name(t.get_name()), "why": "name"}
    if folder:
        reg = unreal.AssetRegistryHelpers.get_asset_registry()
        for ad in reg.get_assets_by_path(folder.rstrip("/"), recursive=True):
            cls = str(ad.asset_class_path.asset_name)
            if cls not in ("Texture2D",):
                continue
            t = unreal.load_asset(f"{ad.package_name}.{ad.asset_name}")
            if t is not None and t.get_path_name() not in found:
                found[t.get_path_name()] = {"texture": t, "role": _role_from_name(t.get_name()), "why": "name"}
    for ip in instances or []:
        mi = load(ip, unreal.MaterialInstance)
        for v in mi.get_editor_property("texture_parameter_values"):
            t = v.get_editor_property("parameter_value")
            if t is None:
                continue
            pname = str(v.get_editor_property("parameter_info").get_editor_property("name"))
            role = _role_from_param(pname)
            entry = found.setdefault(t.get_path_name(), {"texture": t, "role": None, "why": None})
            if role:
                entry["role"], entry["why"] = role, f"{mi.get_name()}.{pname}"
    return found


@expose
def texture_check(paths: list | None = None, folder: str | None = None, instances: list | None = None,
                  roles: dict | None = None, fix: bool = False) -> dict:
    if not (paths or folder or instances):
        raise UserError("pass texture paths, a folder, or material instances whose textures to check")
    found = _collect(paths, folder, instances)
    for path, role in (roles or {}).items():
        t = load(path, unreal.Texture)
        found.setdefault(t.get_path_name(), {"texture": t})
        found[t.get_path_name()].update(role=role, why="given")
    rows, fixed = [], []
    to_fix = []
    for path, e in sorted(found.items()):
        t = e["texture"]
        comp = enum_name(t.get_editor_property("compression_settings"))
        srgb = bool(t.get_editor_property("srgb"))
        row = {"texture": path, "role": e.get("role"), "role_from": e.get("why"), "compression": comp, "srgb": srgb}
        if e.get("role") in RULES:
            want_comp, want_srgb = RULES[e["role"]]
            row["expected"] = {"compression": want_comp, "srgb": want_srgb}
            row["ok"] = comp == want_comp and srgb == want_srgb
            if not row["ok"]:
                to_fix.append((t, want_comp, want_srgb, row))
        else:
            row["ok"] = None
        rows.append(row)
    if fix and to_fix:
        with transaction(f"ue-mcp: fix {len(to_fix)} texture settings"):
            for t, want_comp, want_srgb, row in to_fix:
                modify(t)
                t.set_editor_property("compression_settings", getattr(unreal.TextureCompressionSettings, want_comp))
                t.set_editor_property("srgb", want_srgb)
                row["fixed"] = True
                fixed.append(t.get_path_name())
    return {"textures": rows, "problems": [r["texture"] for r in rows if r["ok"] is False and not r.get("fixed")],
            "fixed": fixed, "unknown_role": [r["texture"] for r in rows if r["ok"] is None], "saved": False}
