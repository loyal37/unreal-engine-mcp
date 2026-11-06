"""Material inspection and editing.

Instance-editor visibility follows `FMaterialEditorUtilities::GetVisibleMaterialParameters`
(UE 5.6 `MaterialEditorUtilities.cpp`): starting at the material outputs, walk inputs,
take only the active branch of static switches (using the instance's values), step into
function calls and back out through function inputs, and trace reroutes. Every parameter
node met on the way is visible. When the HT plugin's script library is loaded the engine
function itself answers; this module's walk is the fallback.
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict

import unreal

from .core import (UserError, class_name, enum_name, expose, load, modify, refuse_if_open, to_json,
                   transaction)
from .assets import parent_chain

MEL = unreal.MaterialEditingLibrary

# Material outputs whose input exists in UE 5.6 (UMaterial::GetExpressionInputDescription).
# GetMaterialPropertyInputNode dereferences the input without a null check, so never pass
# anything else (MP_DiffuseColor, MP_SpecularColor, MP_CustomOutput, MP_MAX would crash).
OUTPUTS = ["BaseColor", "Metallic", "Specular", "Roughness", "Anisotropy", "EmissiveColor", "Opacity",
           "OpacityMask", "Normal", "Tangent", "WorldPositionOffset", "Displacement", "SubsurfaceColor",
           "CustomData0", "CustomData1", "AmbientOcclusion", "Refraction", "PixelDepthOffset", "ShadingModel",
           "SurfaceThickness", "FrontMaterial", "MaterialAttributes"] + [f"CustomizedUVs{i}" for i in range(8)]

PARAM_TYPES = {
    "scalar": "MaterialExpressionScalarParameter",
    "vector": "MaterialExpressionVectorParameter",
    "texture": "MaterialExpressionTextureSampleParameter2D",
    "texture_object": "MaterialExpressionTextureObjectParameter",
    "static_switch": "MaterialExpressionStaticSwitchParameter",
    "static_bool": "MaterialExpressionStaticBoolParameter",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


_OUTPUT_ENUMS = None


def output_properties() -> list[tuple[str, object]]:
    global _OUTPUT_ENUMS
    if _OUTPUT_ENUMS is None:
        members = {_norm(n[3:]): getattr(unreal.MaterialProperty, n)
                   for n in dir(unreal.MaterialProperty) if n.startswith("MP_")}
        _OUTPUT_ENUMS = [(name, members[_norm(name)]) for name in OUTPUTS if _norm(name) in members]
    return _OUTPUT_ENUMS


def output_enum(name: str):
    for n, e in output_properties():
        if _norm(n) == _norm(name) or _norm(n) == _norm(name.replace("Color", "")) or \
                (_norm(name) == "emissive" and n == "EmissiveColor") or (_norm(name) == "diffuse" and n == "BaseColor"):
            return n, e
    raise UserError(f"unknown material output {name!r}; use one of {', '.join(n for n, _ in output_properties())}")


def _cls(name: str):
    return getattr(unreal, name, None)


def _isinstance(obj, *names: str) -> bool:
    classes = tuple(c for c in (_cls(n) for n in names) if c is not None)
    return bool(classes) and isinstance(obj, classes)


# ---------------------------------------------------------------------------- graph access
class Graph:
    """Expressions of materials and material functions, gathered once per call."""

    def __init__(self):
        self._index = None
        self.notes: list[str] = []
        self.exact = hasattr(unreal, "HTModScriptLibrary") and hasattr(unreal.HTModScriptLibrary, "get_material_expressions")

    def expressions(self, owner) -> list:
        if self.exact:
            return [e for e in unreal.HTModScriptLibrary.get_material_expressions(owner) if e is not None]
        if self._index is None:
            if not hasattr(unreal, "ObjectIterator"):
                raise UserError("this editor's Python has no ObjectIterator; load the HT script library")
            idx = defaultdict(list)
            for e in unreal.ObjectIterator(unreal.MaterialExpression):
                outer = e.get_outer()
                if outer is not None:
                    idx[outer.get_path_name()].append(e)
            self._index = idx
        found = [e for e in self._index.get(owner.get_path_name(), []) if not _isinstance(e, "MaterialExpressionComment")]
        if isinstance(owner, unreal.Material):
            expected = MEL.get_num_material_expressions(owner)
            if expected != len(found):
                self.notes.append(
                    f"{owner.get_name()}: found {len(found)} nodes but the material lists {expected}; deleted nodes "
                    "kept by the undo history may be included. The HT script library gives the exact list.")
        return found

    def comments(self, owner) -> list:
        if self._index is None and not self.exact:
            self.expressions(owner)
        if self.exact:
            return []
        return [e for e in self._index.get(owner.get_path_name(), []) if _isinstance(e, "MaterialExpressionComment")]


def node_id(e) -> str:
    return e.get_name()


def inputs_of(owner_material, e) -> list[tuple[str, object, str]]:
    """(input name, source expression or None, source output name) for each input pin."""
    names = list(MEL.get_material_expression_input_names(e))
    sources = list(MEL.get_inputs_for_material_expression(owner_material, e))
    out = []
    for i, src in enumerate(sources):
        out_name = ""
        if src is not None:
            r = MEL.get_input_node_output_name_for_material_expression(e, src)
            if isinstance(r, tuple):
                out_name = next((x for x in r if isinstance(x, str)), "")
            elif isinstance(r, str):
                out_name = r
        out.append((names[i] if i < len(names) else str(i), src, out_name))
    return out


def output_source(material, prop) -> tuple[object, str]:
    e = MEL.get_material_property_input_node(material, prop)
    if e is None:
        return None, ""
    name = MEL.get_material_property_input_node_output_name(material, prop)
    if isinstance(name, tuple):
        name = next((x for x in name if isinstance(x, str)), "")
    return e, name or ""


def param_kind(e) -> str | None:
    if _isinstance(e, "MaterialExpressionStaticSwitchParameter"):
        return "static_switch"
    if _isinstance(e, "MaterialExpressionStaticBoolParameter"):
        return "static_bool"
    if _isinstance(e, "MaterialExpressionStaticComponentMaskParameter"):
        return "static_component_mask"
    if _isinstance(e, "MaterialExpressionScalarParameter"):
        return "scalar"
    if _isinstance(e, "MaterialExpressionVectorParameter"):
        return "vector"
    if _isinstance(e, "MaterialExpressionDoubleVectorParameter"):
        return "double_vector"
    if _isinstance(e, "MaterialExpressionTextureObjectParameter", "MaterialExpressionTextureSampleParameter"):
        return "texture"
    if _isinstance(e, "MaterialExpressionRuntimeVirtualTextureSampleParameter"):
        return "runtime_virtual_texture"
    if _isinstance(e, "MaterialExpressionSparseVolumeTextureSampleParameter"):
        return "sparse_volume_texture"
    if _isinstance(e, "MaterialExpressionFontSampleParameter"):
        return "font"
    if _isinstance(e, "MaterialExpressionTextureCollectionParameter"):
        return "texture_collection"
    if _isinstance(e, "MaterialExpressionParameter"):
        return "other"
    return None


def _prop(e, name, default=None):
    try:
        return e.get_editor_property(name)
    except Exception:  # noqa: BLE001
        return default


def param_info(e) -> dict:
    kind = param_kind(e)
    group = str(_prop(e, "group", "") or "")
    info = {"name": str(_prop(e, "parameter_name", "")), "type": kind,
            "group": "" if group == "None" else group, "sort_priority": _prop(e, "sort_priority", 0)}
    if kind in ("scalar", "vector", "static_switch", "static_bool", "double_vector"):
        info["default"] = to_json(_prop(e, "default_value"))
    elif kind == "texture":
        tex = _prop(e, "texture")
        info["default"] = tex.get_path_name() if tex else None
    elif kind == "static_component_mask":
        info["default"] = [bool(_prop(e, f"default_{c}", False)) for c in "rgba"]
    desc = _prop(e, "desc")
    if desc:
        info["description"] = str(desc)
    return info


def node_summary(e) -> dict:
    cls = class_name(e)
    d = {"id": node_id(e), "class": cls.replace("MaterialExpression", "", 1) or cls}
    try:
        x, y = MEL.get_material_expression_node_position(e)
        d["pos"] = [x, y]
    except Exception:  # noqa: BLE001
        pass
    kind = param_kind(e)
    if kind:
        p = param_info(e)
        d["parameter"] = p["name"]
        d["parameter_type"] = kind
        if p["group"]:
            d["group"] = p["group"]
        if "default" in p:
            d["default"] = p["default"]
    elif _isinstance(e, "MaterialExpressionConstant"):
        d["value"] = _prop(e, "r")
    elif _isinstance(e, "MaterialExpressionConstant2Vector"):
        d["value"] = [_prop(e, "r"), _prop(e, "g")]
    elif _isinstance(e, "MaterialExpressionConstant3Vector", "MaterialExpressionConstant4Vector"):
        d["value"] = to_json(_prop(e, "constant"))
    elif _isinstance(e, "MaterialExpressionTextureBase"):
        t = _prop(e, "texture")
        d["texture"] = t.get_path_name() if t else None
    elif _isinstance(e, "MaterialExpressionMaterialFunctionCall"):
        f = _prop(e, "material_function")
        d["function"] = f.get_path_name() if f else None
    elif _isinstance(e, "MaterialExpressionFunctionInput", "MaterialExpressionFunctionOutput"):
        d["pin_name"] = str(_prop(e, "input_name", None) or _prop(e, "output_name", ""))
    elif _isinstance(e, "MaterialExpressionCustom"):
        code = str(_prop(e, "code", "") or "")
        d["code"] = code if len(code) < 400 else code[:400] + "..."
    elif _isinstance(e, "MaterialExpressionStaticSwitch"):
        d["default"] = _prop(e, "default_value")
    desc = _prop(e, "desc")
    if desc:
        d["description"] = str(desc)
    return d


# ---------------------------------------------------------------------------- visibility walk
class _State:
    def __init__(self, call):
        self.call = call
        self.visited = set()


class VisibilityWalk:
    """Python port of GetVisibleMaterialParameters (layers are not followed)."""

    def __init__(self, graph: Graph, base_material, instance=None):
        self.graph = graph
        self.base = base_material
        self.instance = instance
        self.visible: set[str] = set()
        self.reached: set[str] = set()
        self.notes: set[str] = set()
        self.stack = [_State(None)]
        self._function_outputs: dict = {}

    def run(self):
        for _name, prop in output_properties():
            e, out = output_source(self.base, prop)
            self.visit(e, out)
        for e in self.graph.expressions(self.base):
            if _isinstance(e, "MaterialExpressionCustomOutput"):
                self.visit(e, "")
        return self

    # values ---------------------------------------------------------------
    def static_switch_value(self, name: str) -> bool:
        if self.instance is not None:
            r = MEL.get_material_instance_static_switch_parameter_value(self.instance, name)
        else:
            r = MEL.get_material_default_static_switch_parameter_value(self.base, name)
        return bool(r[0] if isinstance(r, tuple) else r)

    def eval_static(self, e):
        """Value of a static bool expression (GetStaticSwitchExpressionValue), or None."""
        if e is None:
            return None
        if _isinstance(e, "MaterialExpressionReroute"):
            ins = inputs_of(self.base, e)
            return self.eval_static(ins[0][1] if ins else None)
        if _isinstance(e, "MaterialExpressionNamedRerouteUsage"):
            decl = _prop(e, "declaration")
            ins = inputs_of(self.base, decl) if decl else []
            return self.eval_static(ins[0][1] if ins else None)
        if _isinstance(e, "MaterialExpressionFunctionInput") and len(self.stack) > 1:
            state = self.stack.pop()
            try:
                src = self._call_input(state.call, str(_prop(e, "input_name", "")))
                if src is not None and src[0] is not None:
                    return self.eval_static(src[0])
                ins = inputs_of(self.base, e)
                return self.eval_static(ins[0][1] if ins else None)
            finally:
                self.stack.append(state)
        if _isinstance(e, "MaterialExpressionStaticBoolParameter"):
            return self.static_switch_value(str(_prop(e, "parameter_name", "")))
        if _isinstance(e, "MaterialExpressionStaticBool"):
            return bool(_prop(e, "value", False))
        if _isinstance(e, "MaterialExpressionStaticSwitch"):
            ins = inputs_of(self.base, e)
            v = bool(_prop(e, "default_value", False))
            if len(ins) > 2 and ins[2][1] is not None:
                got = self.eval_static(ins[2][1])
                if got is not None:
                    v = got
            branch = ins[0] if v else ins[1]
            return self.eval_static(branch[1])
        return None

    # walk -----------------------------------------------------------------
    def _call_input(self, call, input_name: str):
        for name, src, out in inputs_of(self.base, call):
            if _norm(name) == _norm(input_name):
                return src, out
        return None

    def _function_output(self, function, output_name: str):
        key = function.get_path_name()
        if key not in self._function_outputs:
            outs = [e for e in self.graph.expressions(function) if _isinstance(e, "MaterialExpressionFunctionOutput")]
            self._function_outputs[key] = outs
        outs = self._function_outputs[key]
        for o in outs:
            if _norm(str(_prop(o, "output_name", ""))) == _norm(output_name):
                return o
        if len(outs) == 1:
            return outs[0]
        if outs and not output_name:
            return outs[0]
        return None

    def visit(self, e, out_name: str = "") -> None:
        if e is None:
            return
        state = self.stack[-1]
        key = (e.get_path_name(), out_name or "")
        if key in state.visited:
            return
        state.visited.add(key)
        self.reached.add(e.get_path_name())
        kind = param_kind(e)
        if kind:
            self.visible.add(str(_prop(e, "parameter_name", "")))
        if _isinstance(e, "MaterialExpressionStaticSwitchParameter"):
            ins = inputs_of(self.base, e)
            value = self.static_switch_value(str(_prop(e, "parameter_name", "")))
            branch = ins[0] if value else ins[1]
            self.visit(branch[1], branch[2])
        elif _isinstance(e, "MaterialExpressionStaticSwitch"):
            ins = inputs_of(self.base, e)
            value = bool(_prop(e, "default_value", False))
            if len(ins) > 2 and ins[2][1] is not None:
                got = self.eval_static(ins[2][1])
                if got is not None:
                    value = got
                self.visit(ins[2][1], ins[2][2])
            branch = ins[0] if value else ins[1]
            self.visit(branch[1], branch[2])
        elif _isinstance(e, "MaterialExpressionMaterialFunctionCall"):
            function = _prop(e, "material_function")
            if function is None:
                return
            if not isinstance(function, unreal.MaterialFunction):
                self.notes.add(f"{function.get_path_name()}: material function instances are not followed")
                return
            target = self._function_output(function, out_name)
            if target is None:
                self.notes.add(f"{function.get_path_name()}: output {out_name!r} not found")
                return
            self.stack.append(_State(e))
            try:
                self.visit(target, "")
            finally:
                self.stack.pop()
        elif _isinstance(e, "MaterialExpressionMaterialAttributeLayers"):
            self.notes.add("material attribute layers are not followed by the Python walk")
        elif _isinstance(e, "MaterialExpressionFunctionInput") and len(self.stack) > 1:
            ins = inputs_of(self.base, e)  # the Preview input
            if ins:
                self.visit(ins[0][1], ins[0][2])
            state = self.stack.pop()
            try:
                src = self._call_input(state.call, str(_prop(e, "input_name", "")))
                if src is not None:
                    self.visit(src[0], src[1])
            finally:
                self.stack.append(state)
        elif _isinstance(e, "MaterialExpressionNamedRerouteUsage"):
            decl = _prop(e, "declaration")
            if decl is not None:
                self.visit(decl, "")
        else:
            for _name, src, out in inputs_of(self.base, e):
                self.visit(src, out)


def visible_parameters(asset, graph: Graph) -> tuple[set[str], str, list[str]]:
    """Names the instance editor shows for `asset` (an instance, or a material seen
    through a fresh child instance), the method used, and notes."""
    lib = getattr(unreal, "HTModScriptLibrary", None)
    if lib is not None and hasattr(lib, "get_visible_material_parameters"):
        names = lib.get_visible_material_parameters(asset)
        return {str(n) for n in names}, "engine (FMaterialEditorUtilities::GetVisibleMaterialParameters)", []
    base = asset.get_base_material() if isinstance(asset, unreal.MaterialInstance) else asset
    instance = asset if isinstance(asset, unreal.MaterialInstance) else None
    walk = VisibilityWalk(graph, base, instance).run()
    return walk.visible, "python port of GetVisibleMaterialParameters", sorted(walk.notes)


# ---------------------------------------------------------------------------- parameter listing
def _functions_used(graph: Graph, owner, seen: set) -> list:
    out = []
    for e in graph.expressions(owner):
        if _isinstance(e, "MaterialExpressionMaterialFunctionCall"):
            f = _prop(e, "material_function")
            if isinstance(f, unreal.MaterialFunction) and f.get_path_name() not in seen:
                seen.add(f.get_path_name())
                out.append(f)
                out.extend(_functions_used(graph, f, seen))
    return out


def parameter_nodes(graph: Graph, base_material) -> dict[tuple[str, str], dict]:
    """(type, name) -> info of the first node, plus every node and owner."""
    params: dict[tuple[str, str], dict] = {}
    owners = [base_material] + _functions_used(graph, base_material, set())
    for owner in owners:
        for e in graph.expressions(owner):
            kind = param_kind(e)
            if not kind:
                continue
            info = param_info(e)
            key = (kind, info["name"])
            entry = params.get(key)
            if entry is None:
                entry = dict(info)
                entry["source"] = owner.get_path_name()
                entry["nodes"] = []
                params[key] = entry
            entry["nodes"].append(e)
    return params


def _overrides(mi) -> dict[tuple[str, str], object]:
    out = {}
    for prop, kind in (("scalar_parameter_values", "scalar"), ("vector_parameter_values", "vector"),
                       ("texture_parameter_values", "texture"), ("double_vector_parameter_values", "double_vector")):
        try:
            values = mi.get_editor_property(prop)
        except Exception:  # noqa: BLE001
            continue
        for v in values:
            info = v.get_editor_property("parameter_info")
            assoc = enum_name(info.get_editor_property("association"))
            if assoc != "GLOBAL_PARAMETER":
                continue
            out[(kind, str(info.get_editor_property("name")))] = to_json(v.get_editor_property("parameter_value"))
    return out


def _effective(asset, kind: str, name: str):
    try:
        if isinstance(asset, unreal.MaterialInstance):
            fn = {"scalar": MEL.get_material_instance_scalar_parameter_value,
                  "vector": MEL.get_material_instance_vector_parameter_value,
                  "texture": MEL.get_material_instance_texture_parameter_value,
                  "static_switch": MEL.get_material_instance_static_switch_parameter_value,
                  "static_bool": MEL.get_material_instance_static_switch_parameter_value}.get(kind)
        else:
            fn = {"scalar": MEL.get_material_default_scalar_parameter_value,
                  "vector": MEL.get_material_default_vector_parameter_value,
                  "texture": MEL.get_material_default_texture_parameter_value,
                  "static_switch": MEL.get_material_default_static_switch_parameter_value,
                  "static_bool": MEL.get_material_default_static_switch_parameter_value}.get(kind)
        if fn is None:
            return None
        v = fn(asset, name)
        if isinstance(v, tuple):
            v = v[0]
        return to_json(v)
    except Exception:  # noqa: BLE001
        return None


def _engine_static_override(mi, name: str):
    lib = getattr(unreal, "HTModScriptLibrary", None)
    if lib is not None and hasattr(lib, "is_static_switch_overridden"):
        return bool(lib.is_static_switch_overridden(mi, name))
    return None


@expose
def material_parameters(path: str, only_hidden: bool = False) -> dict:
    asset = load(path, unreal.MaterialInterface)
    is_instance = isinstance(asset, unreal.MaterialInstance)
    base = asset.get_base_material()
    if base is None:
        raise UserError(f"{asset.get_path_name()} has no base material")
    graph = Graph()
    params = parameter_nodes(graph, base)
    visible, method, notes = visible_parameters(asset, graph)
    chain = parent_chain(asset) if is_instance else []
    instances = [asset] if is_instance else []
    cur = asset
    while is_instance and isinstance(cur, unreal.MaterialInstance):
        cur = cur.get_editor_property("parent")
        if isinstance(cur, unreal.MaterialInstance):
            instances.append(cur)
    overrides = [(mi.get_path_name(), _overrides(mi)) for mi in instances]
    engine_names = set()
    for getter in ("get_scalar_parameter_names", "get_vector_parameter_names", "get_texture_parameter_names",
                   "get_static_switch_parameter_names"):
        try:
            engine_names.update(str(n) for n in getattr(MEL, getter)(asset))
        except Exception:  # noqa: BLE001
            pass
    rows = []
    for (kind, name), p in sorted(params.items(), key=lambda kv: (kv[1]["group"].casefold(), kv[1]["sort_priority"] or 0, kv[0][1].casefold())):
        row = {k: v for k, v in p.items() if k != "nodes"}
        row["node_count"] = len(p["nodes"])
        row["visible_in_instance_editor"] = name in visible
        if is_instance:
            row["value"] = _effective(asset, kind, name)
            own = overrides[0][1] if overrides else {}
            if kind in ("static_switch", "static_bool"):
                exact = _engine_static_override(asset, name)
                if exact is not None:
                    row["overridden_here"] = exact
                else:
                    parent = asset.get_editor_property("parent")
                    parent_value = _effective(parent, kind, name) if parent else p.get("default")
                    row["overridden_here"] = None
                    row["differs_from_parent"] = row["value"] != parent_value
            else:
                row["overridden_here"] = (kind, name) in own
                if row["overridden_here"]:
                    row["override"] = own[(kind, name)]
                else:
                    for mi_path, ov in overrides[1:]:
                        if (kind, name) in ov:
                            row["inherited_from"] = mi_path
                            break
        if only_hidden and row["visible_in_instance_editor"]:
            continue
        rows.append(row)
    hidden = sorted({name for (kind, name) in params if name not in visible}, key=str.casefold)
    return {
        "asset": asset.get_path_name(),
        "class": class_name(asset),
        "base_material": base.get_path_name(),
        "parent_chain": chain,
        "visibility_method": method,
        "notes": notes + graph.notes,
        "summary": {"parameters": len(params), "visible": len([1 for k in params if k[1] in visible]),
                    "hidden_from_instance_editor": hidden,
                    "listed_by_engine_but_no_node": sorted(engine_names - {k[1] for k in params}, key=str.casefold)},
        "parameters": rows,
    }


# ---------------------------------------------------------------------------- graph dump
@expose
def material_graph(path: str, include_functions: bool = False) -> dict:
    asset = load(path, (unreal.MaterialInterface, unreal.MaterialFunction))
    graph = Graph()
    if isinstance(asset, unreal.MaterialFunction):
        owner, base, instance = asset, None, None
    else:
        base = asset.get_base_material()
        owner = base
        instance = asset if isinstance(asset, unreal.MaterialInstance) else None
    connect_material = base or _any_material()
    nodes = []
    for e in graph.expressions(owner):
        d = node_summary(e)
        ins = []
        for name, src, out in inputs_of(connect_material, e):
            if src is not None:
                ins.append({"input": name, "from": node_id(src), "output": out})
        if ins:
            d["inputs"] = ins
        nodes.append(d)
    result = {"asset": asset.get_path_name(), "graph_of": owner.get_path_name(), "node_count": len(nodes)}
    if base is not None:
        outputs = []
        for name, prop in output_properties():
            e, out = output_source(base, prop)
            if e is not None:
                outputs.append({"output": name, "from": node_id(e), "from_output": out})
        result["material_outputs"] = outputs
        walk = VisibilityWalk(graph, base, instance).run()
        reached = walk.reached
        for d, e in zip(nodes, graph.expressions(owner)):
            d["reaches_output"] = e.get_path_name() in reached
        result["not_reaching_outputs"] = [d["id"] for d in nodes if not d.get("reaches_output")]
    result["nodes"] = nodes
    comments = []
    for c in graph.comments(owner):
        try:
            x, y = MEL.get_material_expression_node_position(c)
        except Exception:  # noqa: BLE001
            x = y = None
        comments.append({"id": node_id(c), "text": str(_prop(c, "text", "")), "pos": [x, y],
                         "size": [_prop(c, "size_x"), _prop(c, "size_y")]})
    if comments:
        result["comments"] = comments
    if include_functions and base is not None:
        result["functions"] = []
        for f in _functions_used(graph, base, set()):
            result["functions"].append({"function": f.get_path_name(),
                                        "nodes": [node_summary(e) for e in graph.expressions(f)]})
    if graph.notes:
        result["notes"] = graph.notes
    return result


def _any_material():
    return unreal.load_asset("/Engine/EngineMaterials/DefaultMaterial.DefaultMaterial")


def find_node(graph: Graph, owner, ident: str):
    nodes = graph.expressions(owner)
    for e in nodes:
        if node_id(e) == ident:
            return e
    by_param = [e for e in nodes if param_kind(e) and str(_prop(e, "parameter_name", "")) == ident]
    if len(by_param) == 1:
        return by_param[0]
    close = difflib.get_close_matches(ident, [node_id(e) for e in nodes], n=5)
    raise UserError(f"no node {ident!r} in {owner.get_name()}" + (f"; close: {', '.join(close)}" if close else ""))


# ---------------------------------------------------------------------------- editing
def _set_param_default(e, kind: str, default):
    if default is None:
        return
    if kind == "scalar":
        e.set_editor_property("default_value", float(default))
    elif kind == "vector":
        e.set_editor_property("default_value", color(default))
    elif kind in ("texture", "texture_object"):
        tex = load(default, unreal.Texture)
        e.set_editor_property("texture", tex)
    elif kind in ("static_switch", "static_bool"):
        e.set_editor_property("default_value", bool(default))


def color(value) -> unreal.LinearColor:
    if isinstance(value, dict):
        return unreal.LinearColor(float(value.get("r", 0)), float(value.get("g", 0)), float(value.get("b", 0)),
                                  float(value.get("a", 1)))
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        v = [float(x) for x in value] + ([1.0] if len(value) == 3 else [])
        return unreal.LinearColor(*v)
    if isinstance(value, (int, float)):
        return unreal.LinearColor(float(value), float(value), float(value), 1.0)
    raise UserError(f"not a color: {value!r} (use [r, g, b] or [r, g, b, a])")


def _new_position(graph: Graph, material) -> tuple[int, int]:
    xs, ys = [], []
    for e in graph.expressions(material):
        try:
            x, y = MEL.get_material_expression_node_position(e)
            xs.append(x)
            ys.append(y)
        except Exception:  # noqa: BLE001
            pass
    if not xs:
        return -400, 0
    return min(xs) - 400, max(ys) + 200


def _connect(material, graph: Graph, src, src_output: str, to: dict | str | None) -> dict | None:
    """Connects src to a material output ("EmissiveColor") or a node input ({"node", "input"})."""
    if not to:
        return None
    if isinstance(to, str) or "output" in to:
        name = to if isinstance(to, str) else to["output"]
        out_name, prop = output_enum(name)
        before, before_out = output_source(material, prop)
        ok = MEL.connect_material_property(src, src_output or "", prop)
        if not ok:
            raise UserError(f"could not connect {node_id(src)}.{src_output or '(default)'} to output {out_name}")
        return {"to_output": out_name, "replaced": node_id(before) if before else None,
                "replaced_output": before_out if before else None}
    target = find_node(graph, material, to["node"])
    modify(target)
    pin = to.get("input", "")
    replaced = None
    for name, s, out in inputs_of(material, target):
        if name == pin and s is not None:
            replaced = {"node": node_id(s), "output": out}
    ok = MEL.connect_material_expressions(src, src_output or "", target, pin)
    if not ok:
        pins = [n for n, _s, _o in inputs_of(material, target)]
        raise UserError(f"could not connect to {node_id(target)} input {pin!r}; inputs: {pins}")
    return {"to_node": node_id(target), "to_input": pin, "replaced": replaced}


@expose
def material_add_parameter(material: str, type: str, name: str, group: str | None = None, default=None,
                           sort_priority: int | None = None, position: list | None = None,
                           connect_to=None, output: str = "", allow_open_editor: bool = False,
                           allow_duplicate_name: bool = False, recompile: bool = True) -> dict:
    mat = load(material, unreal.Material)
    refuse_if_open(mat, allow_open_editor, "Material Editor")
    cls_name = PARAM_TYPES.get(type)
    if cls_name is None:
        raise UserError(f"type must be one of {', '.join(PARAM_TYPES)}")
    graph = Graph()
    existing = [e for e in graph.expressions(mat) if param_kind(e) and str(_prop(e, "parameter_name", "")) == name]
    if existing and not allow_duplicate_name:
        raise UserError(f"{mat.get_name()} already has a parameter node named {name!r} ({node_id(existing[0])}); "
                        "connect that one with material_connect, or pass allow_duplicate_name=true")
    x, y = (int(position[0]), int(position[1])) if position else _new_position(graph, mat)
    with transaction(f"ue-mcp: add parameter {name} to {mat.get_name()}"):
        modify(mat)
        e = MEL.create_material_expression(mat, getattr(unreal, cls_name), x, y)
        modify(e)
        e.set_editor_property("parameter_name", name)
        if group is not None:
            e.set_editor_property("group", group)
        if sort_priority is not None:
            e.set_editor_property("sort_priority", int(sort_priority))
        _set_param_default(e, type, default)
        connection = _connect(mat, Graph(), e, output, connect_to)
    if recompile:
        MEL.recompile_material(mat)
    visible, method, notes = visible_parameters(mat, Graph())
    return {"material": mat.get_path_name(), "node": node_id(e), "position": [x, y], "connection": connection,
            "visible_in_instance_editor": name in visible, "visibility_method": method, "notes": notes,
            "saved": False, "undo": "Ctrl+Z in the editor undoes this"}


@expose
def material_connect(material: str, from_node: str, to, from_output: str = "", allow_open_editor: bool = False,
                     recompile: bool = True) -> dict:
    mat = load(material, unreal.Material)
    refuse_if_open(mat, allow_open_editor, "Material Editor")
    graph = Graph()
    src = find_node(graph, mat, from_node)
    with transaction(f"ue-mcp: connect {from_node} in {mat.get_name()}"):
        modify(mat)
        modify(src)
        connection = _connect(mat, graph, src, from_output, to)
    if recompile:
        MEL.recompile_material(mat)
    return {"material": mat.get_path_name(), "connection": connection, "saved": False}


@expose
def material_expose_parameters(material: str, parameters: list[dict], output: str = "EmissiveColor",
                               group: str | None = None, allow_open_editor: bool = False) -> dict:
    """Adds parameters so instances can override them by name, wired so the material's
    result stays the same: sum(parameter components) * 0 + <whatever fed the output>."""
    mat = load(material, unreal.Material)
    refuse_if_open(mat, allow_open_editor, "Material Editor")
    if not parameters:
        raise UserError("pass at least one parameter")
    graph = Graph()
    out_name, prop = output_enum(output)
    existing_names = {str(_prop(e, "parameter_name", "")) for e in graph.expressions(mat) if param_kind(e)}
    x0, y0 = _new_position(graph, mat)
    created, reused = [], []
    with transaction(f"ue-mcp: expose {len(parameters)} parameters on {mat.get_name()}"):
        modify(mat)
        terms = []
        y = y0
        for p in parameters:
            kind = p.get("type", "scalar")
            if kind not in ("scalar", "vector", "texture"):
                raise UserError(f"{p.get('name')}: only scalar, vector and texture parameters can be exposed this way")
            pname = p["name"]
            if pname in existing_names:
                node = next(e for e in graph.expressions(mat) if str(_prop(e, "parameter_name", "")) == pname)
                reused.append(pname)
            else:
                node = MEL.create_material_expression(mat, getattr(unreal, PARAM_TYPES[kind]), x0, y)
                modify(node)
                node.set_editor_property("parameter_name", pname)
                if p.get("group", group) is not None:
                    node.set_editor_property("group", p.get("group", group))
                if p.get("sort_priority") is not None:
                    node.set_editor_property("sort_priority", int(p["sort_priority"]))
                _set_param_default(node, kind, p.get("default"))
                created.append(pname)
                y += 150 if kind == "scalar" else 250
            terms.append((node, "" if kind == "scalar" else "R"))
        # Sum the terms pairwise, multiply by zero, add to the existing output input.
        acc, acc_out = terms[0]
        ax = x0 + 300
        for node, out in terms[1:]:
            add = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, ax, y0)
            modify(add)
            MEL.connect_material_expressions(acc, acc_out, add, "A")
            MEL.connect_material_expressions(node, out, add, "B")
            acc, acc_out = add, ""
            ax += 150
        zero = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, ax, y0 + 120)
        modify(zero)
        zero.set_editor_property("r", 0.0)
        mul = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, ax + 150, y0)
        modify(mul)
        MEL.connect_material_expressions(acc, acc_out, mul, "A")
        MEL.connect_material_expressions(zero, "", mul, "B")
        before, before_out = output_source(mat, prop)
        final, final_out = mul, ""
        if before is not None:
            add = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, ax + 300, y0)
            modify(add)
            MEL.connect_material_expressions(before, before_out, add, "A")
            MEL.connect_material_expressions(mul, "", add, "B")
            final, final_out = add, ""
        if not MEL.connect_material_property(final, final_out, prop):
            raise UserError(f"could not connect to output {out_name}")
        # (MaterialEditingLibrary would file a comment box as an ordinary node, so the note goes here.)
        mul.set_editor_property("desc", "Exposes parameters to instances: x 0, the result is unchanged")
    MEL.recompile_material(mat)
    visible, method, notes = visible_parameters(mat, Graph())
    names = [p["name"] for p in parameters]
    return {"material": mat.get_path_name(), "output": out_name, "created": created, "reused": reused,
            "kept_existing_input": node_id(before) if before is not None else None,
            "visible_in_instance_editor": {n: n in visible for n in names}, "visibility_method": method,
            "notes": notes, "saved": False}


# ---------------------------------------------------------------------------- instances
def _types_by_name(base) -> dict[str, str]:
    graph = Graph()
    out = {}
    for (kind, name) in parameter_nodes(graph, base):
        out.setdefault(name, kind)
    return out


def _clear_override(mi, kind: str, name: str) -> bool:
    if kind in ("static_switch", "static_bool"):
        lib = getattr(unreal, "HTModScriptLibrary", None)
        if lib is None or not hasattr(lib, "clear_material_instance_parameter"):
            raise UserError(f"clearing the static switch override {name!r} needs the HT script library (HT Blueprint "
                            "Toggle Tool 1.6.4 or later); Python cannot reach static parameter overrides")
        return bool(lib.clear_material_instance_parameter(mi, name))
    prop = {"scalar": "scalar_parameter_values", "vector": "vector_parameter_values",
            "texture": "texture_parameter_values", "double_vector": "double_vector_parameter_values"}.get(kind)
    if prop is None:
        raise UserError(f"cannot clear a {kind} parameter")
    values = list(mi.get_editor_property(prop))
    kept = [v for v in values if not (str(v.get_editor_property("parameter_info").get_editor_property("name")) == name
                                      and enum_name(v.get_editor_property("parameter_info").get_editor_property("association")) == "GLOBAL_PARAMETER")]
    if len(kept) == len(values):
        return False
    mi.set_editor_property(prop, kept)
    return True


def _set_value(mi, kind: str, name: str, value):
    if kind == "scalar":
        return MEL.set_material_instance_scalar_parameter_value(mi, name, float(value))
    if kind == "vector":
        return MEL.set_material_instance_vector_parameter_value(mi, name, color(value))
    if kind == "texture":
        tex = load(value, unreal.Texture) if value else None
        return MEL.set_material_instance_texture_parameter_value(mi, name, tex)
    if kind in ("static_switch", "static_bool"):
        return MEL.set_material_instance_static_switch_parameter_value(mi, name, bool(value))
    raise UserError(f"{name}: setting {kind} parameters is not supported")


@expose
def instance_set_parameters(instance: str, set: dict | None = None, clear: list | None = None,
                            parent: str | None = None, dry_run: bool = False) -> dict:
    mi = load(instance, unreal.MaterialInstanceConstant)
    set = set or {}
    clear = clear or []
    new_parent = load(parent, unreal.MaterialInterface) if parent else None
    base = (new_parent.get_base_material() if new_parent else mi.get_base_material())
    types = _types_by_name(base)
    for name in list(set) + list(clear):
        if name not in types:
            close = difflib.get_close_matches(name, list(types), n=5)
            raise UserError(f"{base.get_name()} has no parameter {name!r}" + (f"; close: {', '.join(close)}" if close else ""))
    before = {n: _effective(mi, types[n], n) for n in list(set) + list(clear)}
    plan = {"instance": mi.get_path_name(), "set": set, "clear": clear,
            "parent": new_parent.get_path_name() if new_parent else None, "before": before}
    if dry_run:
        return {**plan, "dry_run": True}
    cleared = {}
    with transaction(f"ue-mcp: edit {mi.get_name()}"):
        modify(mi)
        if new_parent is not None:
            MEL.set_material_instance_parent(mi, new_parent)
        for name in clear:
            cleared[name] = _clear_override(mi, types[name], name)
        for name, value in set.items():
            _set_value(mi, types[name], name, value)
        MEL.update_material_instance(mi)
    try:
        MEL.rebuild_material_instance_editors(base)
    except Exception:  # noqa: BLE001
        pass
    after = {n: _effective(mi, types[n], n) for n in list(set) + list(clear)}
    return {**plan, "cleared": cleared, "after": after, "saved": False, "undo": "Ctrl+Z in the editor undoes this"}


@expose
def instance_create(parent: str, path: str, set: dict | None = None) -> dict:
    parent_asset = load(parent, unreal.MaterialInterface)
    op = path.strip()
    if "." in op.rsplit("/", 1)[-1]:
        op = op.split(".", 1)[0]
    folder, name = op.rsplit("/", 1)
    if unreal.EditorAssetLibrary.does_asset_exist(op):
        raise UserError(f"{op} already exists")
    # The factory's InitialParent is not exposed to Python: create, then set the parent.
    factory = unreal.MaterialInstanceConstantFactoryNew()
    mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.MaterialInstanceConstant, factory)
    if mi is None:
        raise UserError(f"could not create {op}")
    MEL.set_material_instance_parent(mi, parent_asset)
    MEL.update_material_instance(mi)
    result = {"created": mi.get_path_name(), "parent": parent_asset.get_path_name(), "saved": False,
              "note": "creating an asset is not undoable; delete it in the Content Browser if unwanted"}
    if set:
        result["parameters"] = instance_set_parameters(mi.get_path_name(), set=set)
    return result
