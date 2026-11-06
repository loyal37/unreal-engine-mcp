"""Request dispatch, JSON conversion and shared editor helpers."""
from __future__ import annotations

import contextlib
import json
import os
import time
import traceback

import unreal

FUNCS: dict = {}


class UserError(Exception):
    """A problem the caller can fix (bad path, wrong asset type, ...)."""

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra


def expose(fn):
    FUNCS[fn.__name__] = fn
    return fn


# ---------------------------------------------------------------------------- dispatch
def handle(request_path: str, response_path: str) -> None:
    started = time.time()
    reply: dict
    try:
        with open(request_path, "r", encoding="utf-8") as f:
            req = json.load(f)
    except Exception as e:  # noqa: BLE001
        _write(response_path, {"ok": False, "error": f"cannot read request: {e}"})
        return
    if time.time() > float(req.get("deadline") or 0):
        _write(response_path, {"ok": False, "error": "request expired while waiting in the editor; it was not run"})
        return
    fn = FUNCS.get(req.get("func"))
    if fn is None:
        _write(response_path, {"ok": False, "error": f"unknown helper function {req.get('func')!r}"})
        return
    try:
        result = fn(**(req.get("args") or {}))
        reply = {"ok": True, "result": to_json(result)}
    except UserError as e:
        reply = {"ok": False, "error": str(e), **to_json(e.extra)}
    except TypeError as e:
        reply = {"ok": False, "error": f"bad arguments for {req.get('func')}: {e}", "traceback": traceback.format_exc()}
    except Exception as e:  # noqa: BLE001
        reply = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    reply["elapsed"] = round(time.time() - started, 3)
    _write(response_path, reply)


def _write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------- JSON
def to_json(v, depth: int = 0):
    if depth > 12:
        return str(v)
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 6) if v == v else None
    if isinstance(v, dict):
        return {str(k): to_json(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [to_json(x, depth + 1) for x in v]
    if isinstance(v, (unreal.Name, unreal.Text)):
        return str(v)
    if isinstance(v, unreal.LinearColor):
        return [round(v.r, 6), round(v.g, 6), round(v.b, 6), round(v.a, 6)]
    if isinstance(v, unreal.Color):
        return [v.r, v.g, v.b, v.a]
    if isinstance(v, (unreal.Vector, unreal.Vector4)):
        return [round(c, 6) for c in v.to_tuple()] if hasattr(v, "to_tuple") else str(v)
    if isinstance(v, unreal.Object):
        return v.get_path_name()
    if isinstance(v, unreal.EnumBase):
        return enum_name(v)
    if isinstance(v, unreal.Array) or isinstance(v, unreal.Set):
        return [to_json(x, depth + 1) for x in v]
    if isinstance(v, unreal.Map):
        return {str(k): to_json(x, depth + 1) for k, x in v.items()}
    return str(v)


def enum_name(v) -> str:
    name = getattr(v, "name", None)
    if isinstance(name, str):
        return name
    text = str(v)
    return text.split(".")[-1].split(":")[0].strip("<> ")


# ---------------------------------------------------------------------------- assets
def object_path(path: str) -> str:
    """'/Game/A/B' -> '/Game/A/B.B'; strips Class'...' wrappers."""
    p = (path or "").strip()
    if "'" in p:
        p = p.split("'")[1]
    if not p.startswith("/"):
        raise UserError(f"not an asset path: {path!r} (expected /Game/...)")
    if "." not in p.rsplit("/", 1)[-1]:
        p = p + "." + p.rsplit("/", 1)[-1]
    return p


def package_name(path: str) -> str:
    return object_path(path).split(".", 1)[0]


def load(path: str, expected=None):
    op = object_path(path)
    asset = unreal.load_asset(op)
    if asset is None:
        raise UserError(f"asset not found: {op}")
    if expected is not None and not isinstance(asset, expected):
        names = expected.__name__ if isinstance(expected, type) else "/".join(t.__name__ for t in expected)
        raise UserError(f"{op} is a {asset.get_class().get_name()}, expected {names}")
    return asset


def class_name(obj) -> str:
    return obj.get_class().get_name()


def asset_registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def edited_assets():
    """Assets open in asset editors, or None when this editor cannot tell (Python does not
    expose UAssetEditorSubsystem::GetAllEditedAssets; the HT script library does)."""
    lib = getattr(unreal, "HTModScriptLibrary", None)
    if lib is not None and hasattr(lib, "get_open_asset_editors"):
        return [a for a in lib.get_open_asset_editors() if a is not None]
    return None


def _graph_signature(owner) -> set:
    sig = set()
    for e in unreal.ObjectIterator(unreal.MaterialExpression):
        if e.get_outer() == owner:
            try:
                x, y = unreal.MaterialEditingLibrary.get_material_expression_node_position(e)
            except Exception:  # noqa: BLE001
                x = y = None
            sig.add((e.get_class().get_name(), e.get_name(), x, y))
    return sig


def is_open_in_editor(asset):
    """True/False, or None when unknown. Without the HT script library a Material counts as
    open when a Material Editor preview copy with the same nodes exists (the editor works on
    such a copy), which misses copies the user already changed."""
    lib = getattr(unreal, "HTModScriptLibrary", None)
    if lib is not None and hasattr(lib, "is_asset_open_in_editor"):
        return bool(lib.is_asset_open_in_editor(asset))
    preview_cls = getattr(unreal, "PreviewMaterial", None)
    if not isinstance(asset, unreal.Material) or preview_cls is None:
        return None
    mine = _graph_signature(asset)
    if not mine:
        return None
    for preview in unreal.ObjectIterator(preview_cls):
        if preview is not None and preview != asset and _graph_signature(preview) == mine:
            return True
    return False


def dirty_package_names() -> list[str]:
    out = []
    for getter in ("get_dirty_content_packages", "get_dirty_map_packages"):
        try:
            for p in getattr(unreal.EditorLoadingAndSavingUtils, getter)():
                out.append(p.get_name())
        except Exception:  # noqa: BLE001
            pass
    return out


def package_files(pkg: str) -> list[str]:
    """Files on disk of a package under /Game (the editor saves .uasset/.umap, sometimes .uexp/.ubulk)."""
    if not pkg.startswith("/Game/"):
        return []
    content = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
    stem = os.path.normpath(os.path.join(content, pkg[len("/Game/"):].replace("/", os.sep)))
    return [stem + ext for ext in (".uasset", ".umap", ".uexp", ".ubulk", ".uptnl") if os.path.isfile(stem + ext)]


def project_saved_dir() -> str:
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()))


# ---------------------------------------------------------------------------- editing
@contextlib.contextmanager
def transaction(description: str):
    """An editor undo transaction. Objects must be modify()-ed inside it."""
    with unreal.ScopedEditorTransaction(description) as t:
        yield t


def modify(obj) -> None:
    """Records `obj` in the current transaction (MaterialEditingLibrary does not)."""
    if obj is not None and hasattr(obj, "modify"):
        obj.modify()


def refuse_if_open(asset, allow: bool, what: str = "asset") -> None:
    if allow or is_open_in_editor(asset) is not True:
        return
    raise UserError(
        f"{asset.get_path_name()} is open in its editor. The Material Editor works on a copy and would overwrite "
        f"this change when the user presses Apply or Save. Ask the user to close that {what} tab, "
        f"or pass allow_open_editor=true to edit anyway.")
