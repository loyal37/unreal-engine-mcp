"""Live checks of the editing tools against a SCRATCH editor (never the user's project).

    python scripts/scratch_editor.py create --dir <scratch>
    python scripts/scratch_editor.py start  --dir <scratch>
    python tests/live_scratch.py --project <scratch>
    python scripts/scratch_editor.py stop   --dir <scratch>

Builds /Game/McpTest assets programmatically and checks material visibility, instance
overrides, undo, graph edits, mesh slots, textures and saving. Not part of `unittest discover`.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from ue_mcp.app import App  # noqa: E402
from ue_mcp.tools import build  # noqa: E402

import time  # noqa: E402

# A fresh folder per run: deleting and recreating assets of the same name in one editor
# session collides with the objects still in memory.
ROOT = f"/Game/McpTest/Run{int(time.time())}"

SETUP = r'''
import unreal
MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary
fn =tools.create_asset("MF_Test", "{root}", unreal.MaterialFunction, unreal.MaterialFunctionFactoryNew())
p = MEL.create_material_expression_in_function(fn, unreal.MaterialExpressionScalarParameter, -300, 0)
p.set_editor_property("parameter_name", "InFunction")
p.set_editor_property("group", "Fn")
out = MEL.create_material_expression_in_function(fn, unreal.MaterialExpressionFunctionOutput, 0, 0)
out.set_editor_property("output_name", "Result")
MEL.connect_material_expressions(p, "", out, "")
MEL.update_material_function(fn, None)
m = tools.create_asset("M_Test", "{root}", unreal.Material, unreal.MaterialFactoryNew())
call = MEL.create_material_expression(m, unreal.MaterialExpressionMaterialFunctionCall, -400, 300)
call.set_material_function(fn)
MEL.connect_material_property(call, "Result", unreal.MaterialProperty.MP_METALLIC)
sw = MEL.create_material_expression(m, unreal.MaterialExpressionStaticSwitchParameter, -400, 0)
sw.set_editor_property("parameter_name", "UseA")
a = MEL.create_material_expression(m, unreal.MaterialExpressionVectorParameter, -800, -100)
a.set_editor_property("parameter_name", "OnlyWhenOn")
b = MEL.create_material_expression(m, unreal.MaterialExpressionVectorParameter, -800, 100)
b.set_editor_property("parameter_name", "OnlyWhenOff")
MEL.connect_material_expressions(a, "", sw, "True")
MEL.connect_material_expressions(b, "", sw, "False")
MEL.connect_material_property(sw, "", unreal.MaterialProperty.MP_BASE_COLOR)
c = MEL.create_material_expression(m, unreal.MaterialExpressionScalarParameter, -400, 500)
c.set_editor_property("parameter_name", "Connected")
c.set_editor_property("default_value", 0.5)
MEL.connect_material_property(c, "", unreal.MaterialProperty.MP_ROUGHNESS)
loose = MEL.create_material_expression(m, unreal.MaterialExpressionScalarParameter, -400, 700)
loose.set_editor_property("parameter_name", "Loose")
MEL.recompile_material(m)
eal.duplicate_asset("/Engine/EngineMeshes/SkeletalCube", "{root}/SK_Cube")
eal.duplicate_asset("/Engine/EngineResources/DefaultTexture", "{root}/T_Test_n")
eal.save_directory("{root}", only_if_is_dirty=False)
"done"
'''.replace("{root}", ROOT)


class Checker:
    def __init__(self, srv):
        self.srv = srv
        self.failures = []
        self.passed = 0

    def call(self, tool, **args):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r["result"]
        texts = [c["text"] for c in res["content"] if c["type"] == "text"]
        if res["isError"]:
            return {"__error__": "\n".join(texts)}
        try:
            return json.loads(texts[0]) if texts else {}
        except json.JSONDecodeError:
            return {"__text__": texts[0]}

    def check(self, label, cond, detail=None):
        if cond:
            self.passed += 1
            print(f"  ok   {label}")
        else:
            self.failures.append(label)
            print(f"  FAIL {label}" + (f": {json.dumps(detail, ensure_ascii=False)[:1500]}" if detail is not None else ""))


def visible_map(res):
    return {p["name"]: p["visible_in_instance_editor"] for p in res.get("parameters", [])}


def run(c: Checker):
    st = c.call("editor_status")
    c.check("connected to the scratch editor", st.get("connected") and "McpTest" in (st.get("project_file") or ""), st)
    r = c.call("run_python", code=SETUP, timeout=300)
    c.check("setup script", r.get("value") == "done", r)

    m = f"{ROOT}/M_Test"
    r = c.call("material_parameters", path=m)
    vis = visible_map(r)
    c.check("material: connected/switch/function params visible",
            vis.get("Connected") and vis.get("UseA") and vis.get("OnlyWhenOff") and vis.get("InFunction"), r)
    c.check("material: unconnected and inactive-branch params hidden",
            vis.get("Loose") is False and vis.get("OnlyWhenOn") is False, vis)

    mi = f"{ROOT}/MI_Test"
    r = c.call("instance_create", parent=m, path=mi, set={"UseA": True, "Connected": 0.25})
    c.check("instance_create with parameters", "created" in r, r)
    r = c.call("material_parameters", path=mi)
    vis = visible_map(r)
    rows = {p["name"]: p for p in r.get("parameters", [])}
    c.check("instance: switch on shows OnlyWhenOn, hides OnlyWhenOff",
            vis.get("OnlyWhenOn") is True and vis.get("OnlyWhenOff") is False, vis)
    c.check("instance: Connected overridden to 0.25",
            rows.get("Connected", {}).get("overridden_here") is True and abs(rows["Connected"]["value"] - 0.25) < 1e-6, rows.get("Connected"))

    r = c.call("instance_set_parameters", instance=mi, set={"Connected": 0.75}, dry_run=True)
    c.check("dry run changes nothing", r.get("dry_run") and abs(r["before"]["Connected"] - 0.25) < 1e-6, r)
    r = c.call("instance_set_parameters", instance=mi, clear=["Connected"])
    c.check("clear override", r.get("cleared", {}).get("Connected") is True and abs(r["after"]["Connected"] - 0.5) < 1e-6, r)
    c.call("console_command", command="TRANSACTION UNDO")
    r = c.call("material_parameters", path=mi)
    rows = {p["name"]: p for p in r.get("parameters", [])}
    c.check("undo restores the override", rows.get("Connected", {}).get("overridden_here") is True
            and abs(rows["Connected"]["value"] - 0.25) < 1e-6, rows.get("Connected"))
    r = c.call("instance_set_parameters", instance=mi, set={"Nope": 1})
    c.check("unknown parameter refused", "__error__" in r and "Nope" in r["__error__"], r)
    r = c.call("instance_set_parameters", instance=mi, clear=["UseA"])
    c.check("static switch clear: needs script library or works", "__error__" in r or r.get("cleared", {}).get("UseA") is True, r)
    if "__error__" not in r:
        c.call("console_command", command="TRANSACTION UNDO")  # keep UseA on for the later checks

    r = c.call("material_add_parameter", material=m, type="scalar", name="Added", group="New", default=0.3,
               connect_to="Specular")
    c.check("add + connect parameter, visible", r.get("visible_in_instance_editor") is True, r)
    r = c.call("material_add_parameter", material=m, type="scalar", name="Unwired")
    c.check("add without connection is reported hidden", r.get("visible_in_instance_editor") is False, r)
    c.call("console_command", command="TRANSACTION UNDO")
    r = c.call("material_parameters", path=m)
    c.check("undo removes the unwired node", "Unwired" not in visible_map(r), [p["name"] for p in r.get("parameters", [])])
    r = c.call("material_connect", material=m, from_node="Loose", to={"node": "Added", "input": "x"})
    c.check("bad pin is refused", "__error__" in r, r)

    r = c.call("material_expose_parameters", material=m, output="EmissiveColor",
               parameters=[{"name": "Exp1", "type": "scalar", "default": 0.5, "group": "Exposed"},
                           {"name": "Exp2", "type": "vector", "default": [1, 0, 0]},
                           {"name": "Loose", "type": "scalar"}])
    c.check("expose parameters: all visible", r.get("visible_in_instance_editor") == {"Exp1": True, "Exp2": True, "Loose": True}, r)
    c.check("expose parameters: reused existing node", r.get("reused") == ["Loose"], r)

    g = c.call("material_graph", path=m)
    c.check("graph lists outputs and nodes", any(o["output"] == "EmissiveColor" for o in g.get("material_outputs", []))
            and g.get("node_count", 0) >= 10, {k: g.get(k) for k in ("node_count", "material_outputs", "notes")})

    sk = f"{ROOT}/SK_Cube"
    r = c.call("mesh_slots", path=sk)
    c.check("mesh slots", r.get("slot_count", 0) >= 1 and r["slots"][0]["sections"], r)
    r = c.call("mesh_set_slot_materials", path=sk, assignments=[{"slot": 0, "material": mi}])
    c.check("assign slot material", r.get("changes", [{}])[0].get("to", "").endswith("MI_Test"), r)
    r = c.call("slots_override", mesh=sk, materials=[mi], set={"Connected": 0.9}, dry_run=True)
    c.check("slots_override dry run plans one instance", len(r.get("plan", [])) == 1 and r.get("dry_run"), r)
    r = c.call("slots_override", mesh=sk, materials=[mi], set={"Connected": 0.9})
    c.check("slots_override creates and assigns", r.get("assigned") and r["assigned"][0]["to"].endswith("MI_Test_override"), r)
    r = c.call("mesh_slots", path=sk)
    c.check("slot now uses the override", r["slots"][0]["material"].endswith("MI_Test_override"), r["slots"][0])

    t = f"{ROOT}/T_Test_n"
    r = c.call("texture_check", paths=[t])
    c.check("texture role from name, mismatch found", r.get("problems") == [f"{t}.T_Test_n"], r)
    r = c.call("texture_check", paths=[t], fix=True)
    c.check("texture fixed", r.get("fixed") == [f"{t}.T_Test_n"], r)
    r = c.call("texture_check", paths=[t])
    c.check("texture now ok", r.get("problems") == [] and r["textures"][0]["compression"] == "TC_NORMALMAP", r)

    r = c.call("unsaved_assets")
    c.check("unsaved list has the instance", any(p.endswith("MI_Test") for p in r.get("packages", [])), r)
    r = c.call("save_assets", paths=[mi, m])
    c.check("save writes files and backs up the existing material",
            len(r.get("saved", [])) == 2 and any("M_Test.uasset" in b for b in r.get("backups", [])), r)

    r = c.call("open_asset", path=m)
    r = c.call("material_add_parameter", material=m, type="scalar", name="WhileOpen")
    c.check("refuses a material open in the Material Editor", "__error__" in r and "open in its editor" in r["__error__"], r)
    c.call("run_python", code=f"import unreal\nunreal.get_editor_subsystem(unreal.AssetEditorSubsystem)"
                              f".close_all_editors_for_asset(unreal.load_asset('{m}'))")

    r = c.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "screenshot", "arguments": {}}})
    imgs = [x for x in r["result"]["content"] if x["type"] == "image"]
    c.check("screenshot returns an image", bool(imgs), r["result"]["content"][0])

    r = c.call("output_log", lines=20, min_verbosity="Error")
    c.check("output_log filter", "lines" in r, r)


PLUGIN_SETUP = r'''
import unreal
tools = unreal.AssetToolsHelpers.get_asset_tools()
mesh = unreal.load_asset("{root}/SK_Cube")
f = unreal.AnimBlueprintFactory()
f.set_editor_property("target_skeleton", mesh.get_editor_property("skeleton"))
f.set_editor_property("preview_skeletal_mesh", mesh)
f.set_editor_property("parent_class", unreal.AnimInstance)
abp = tools.create_asset("ABP_Test", "{root}", unreal.AnimBlueprint, f)
bf = unreal.BlueprintFactory()
bf.set_editor_property("parent_class", unreal.SaveGame)
sg = tools.create_asset("SG_Test", "{root}", unreal.Blueprint, bf)
unreal.EditorAssetLibrary.save_directory("{root}", only_if_is_dirty=False)
[abp.get_path_name(), sg.get_path_name(), str(mesh.get_editor_property("materials")[0].get_editor_property("material_slot_name"))]
'''.replace("{root}", ROOT)


def run_plugins(c: Checker):
    libs = c.call("script_libraries")
    if not (libs.get("HTModScriptLibrary") and libs.get("NTEPanelScriptLibrary")):
        c.check("script libraries loaded (start the scratch editor with --plugins)", False, libs)
        return
    c.check("script libraries loaded", True)
    m, mi = f"{ROOT}/M_Test", f"{ROOT}/MI_Test"

    r = c.call("material_parameters", path=mi)
    c.check("visibility now comes from the engine", r.get("visibility_method", "").startswith("engine"), r.get("visibility_method"))
    vis = visible_map(r)
    c.check("engine agrees: switch branch and unwired rules", vis.get("OnlyWhenOn") is True and vis.get("OnlyWhenOff") is False
            and vis.get("Connected") is True, vis)
    rows = {p["name"]: p for p in r.get("parameters", [])}
    c.check("static switch override is exact", rows.get("UseA", {}).get("overridden_here") is True, rows.get("UseA"))
    r = c.call("instance_set_parameters", instance=mi, clear=["UseA"])
    c.check("static switch override cleared", r.get("cleared", {}).get("UseA") is True and r["after"]["UseA"] is False, r)
    c.call("console_command", command="TRANSACTION UNDO")
    r = c.call("material_parameters", path=mi)
    rows = {p["name"]: p for p in r.get("parameters", [])}
    c.check("undo restores the static switch override", rows.get("UseA", {}).get("overridden_here") is True, rows.get("UseA"))

    c.call("open_asset", path=m)
    st = c.call("editor_status")
    c.check("open editors are listed exactly", any(p.endswith("M_Test") for p in st.get("open_asset_editors", [])), st.get("open_asset_editors"))
    c.call("run_python", code=f"import unreal\nunreal.get_editor_subsystem(unreal.AssetEditorSubsystem)"
                              f".close_all_editors_for_asset(unreal.load_asset('{m}'))")

    r = c.call("run_python", code=PLUGIN_SETUP, timeout=300)
    c.check("plugin setup (Anim Blueprint, SaveGame)", isinstance(r.get("value"), list), r)
    abp, sg, slot_name = (r.get("value") or [None, None, "x"])
    tg = f"{ROOT}/TG_Test"
    r = c.call("toggle_graph_create", path=tg, anim_blueprint=abp, savegame_blueprint=sg, character_name="Cube")
    c.check("toggle graph created with target", r.get("created") and r.get("target", {}).get("success"), r)
    d = c.call("toggle_graph_describe", path=tg)
    gen = next((n["id"] for n in d.get("nodes", []) if n["kind"] == "generate"), None)
    c.check("describe lists the generate node and the preview mesh", gen and d.get("preview_mesh", "").endswith("SK_Cube"), d)
    ops = [
        {"op": "add", "kind": "part", "alias": "p", "props": {"comment": "Cube", "material_ids": [0]}},
        {"op": "add", "kind": "switch", "alias": "s", "props": {"comment": "Show cube", "key": "alt 6", "option_count": 2,
                                                               "initial_option": 1, "option_labels": ["Off", "On"]}},
        {"op": "connect", "from": "p", "to": "s", "pin": "option 1"},
        {"op": "connect", "from": "s", "to": gen},
    ]
    r = c.call("toggle_graph_edit", path=tg, operations=ops)
    c.check("edit batch builds a tree", r.get("success") and set(r.get("aliases", {})) == {"p", "s"}, r)
    before = len(c.call("toggle_graph_describe", path=tg).get("nodes", []))
    r = c.call("toggle_graph_edit", path=tg, operations=[{"op": "add", "kind": "group"},
                                                         {"op": "connect", "from": "nope", "to": gen}])
    after = len(c.call("toggle_graph_describe", path=tg).get("nodes", []))
    c.check("failed batch is undone as a whole", not r.get("success", True) and r.get("undone") and before == after, r)
    r = c.call("toggle_graph_check", path=tg)
    c.check("check: one variable, valid", r.get("success") and len(r.get("variables", [])) == 1, r)
    r = c.call("toggle_graph_generate", path=tg)
    c.check("generate into the Anim Blueprint (unsaved)", r.get("success") and not r.get("saved"), r)
    r = c.call("panel_scan", anim_blueprint=abp)
    c.check("panel scan finds the tree row", any(row.get("toggle_tree") for row in r.get("rows", [])), r)
    r = c.call("panel_generate", params={"anim_blueprint": abp, "output_path": f"{ROOT}/Panel", "title": "Test"})
    c.check("panel generate", r.get("success") and r.get("widget"), r)
    r = c.call("toggle_generate", params={"mode": "visibility", "anim_blueprint": abp, "savegame_blueprint": sg,
                                         "variable": "LegacyCube", "key": "alt 7", "visibility": "0"})
    c.check("classic visibility toggle", r.get("success"), r)

    r = c.call("material_instance_build", params={"material": m, "name": "MI_Built", "base_color": f"{ROOT}/T_Test_n",
                                                 "id_texture": f"{ROOT}/T_Test_n", "lightmap": f"{ROOT}/T_Test_n",
                                                 "normal": f"{ROOT}/T_Test_n"})
    c.check("four-texture instance builder runs", r.get("success") and r.get("instance", "").endswith("MI_Built"), r)

    r = c.call("run_python", code=f"import unreal\nt = unreal.AssetToolsHelpers.get_asset_tools()\n"
                                  f"x = t.create_asset('{slot_name}', '{ROOT}/Slots', unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())\n"
                                  f"unreal.MaterialEditingLibrary.set_material_instance_parent(x, unreal.load_asset('{m}'))\nx.get_path_name()")
    r = c.call("slot_match_names", folder=f"{ROOT}/Slots", anim_blueprint=abp)
    c.check("slot names matched through the Anim Blueprint's preview mesh",
            r.get("changes") and r["changes"][0]["to"].endswith(slot_name), r)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--plugins", action="store_true", help="also check the HT script library tools")
    a = p.parse_args()
    if "ueproject" in a.project.replace("\\", "/").lower():
        raise SystemExit("refusing to run against a real project; use the scratch project")
    c = Checker(build(App(a.project, timeout=120)))
    try:
        run(c)
        if a.plugins:
            run_plugins(c)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        c.failures.append("exception")
    print(f"\n{c.passed} passed, {len(c.failures)} failed")
    for f in c.failures:
        print("  -", f)
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main())
