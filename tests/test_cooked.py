import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from ue_mcp import cooked  # noqa: E402


class HashTests(unittest.TestCase):
    def test_matches_keys_written_by_the_exporter_window(self):
        # Keys observed in HT's EditorPerProjectUserSettings.ini for these cooked source folders.
        base = "D:/ueproject/HT/Saved/Cooked/Windows/HT/Content/Characters/Player/"
        self.assertEqual("%08X" % cooked.ue_string_hash((base + "004_lacrimosa").lower()), "331B101A")
        self.assertEqual("%08X" % cooked.ue_string_hash((base + "010_nanally").lower()), "AEC9DBFE")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "Proj"
        self.source = self.project / "Saved/Cooked/Windows/Proj/Content/Characters/Player/001_hero"
        (self.source / "ter").mkdir(parents=True)
        for name in ("hero_skin.uasset", "hero_skin.uexp", "ter/MI_a.uasset", "ter/MI_a.uexp", "ter/T_a.uasset",
                     "ter/T_a.uexp", "ter/T_a.ubulk"):
            (self.source / name).write_bytes(b"x")
        content = self.project / "Content/Characters/Player/001_hero/ter"
        content.mkdir(parents=True)
        (content / "MI_a.uasset").write_bytes(b"newer")
        future = time.time() + 100
        os.utime(content / "MI_a.uasset", (future, future))
        self.output = root / "out"
        ini = self.project / "Saved/Config/WindowsEditor"
        ini.mkdir(parents=True)
        key = cooked.selection_key(str(self.source))
        (ini / "EditorPerProjectUserSettings.ini").write_text(
            "[Other]\nX=1\n[HTBlueprintToggleTool.CookedAssetExporter]\n"
            f"CookedSourceDirectory={cooked.normalize_dir(self.source)}\nOutputDirectory={cooked.normalize_dir(self.output)}\n"
            f"{key}=hero_skin.uasset\n{key}=ter/MI_a.uasset\n{key}=ter/T_a.uasset\nSelectedAssets_00000000=other.uasset\n",
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_selection_and_export(self):
        p = cooked.plan(self.project)
        self.assertTrue(p["selection"].startswith("saved selection"))
        self.assertEqual(p["game_path"], "/Game/Characters/Player/001_hero")
        self.assertTrue(p["output_root"].endswith("/out/001_hero"))
        self.assertEqual([len(r["files"]) for r in p["assets"]], [2, 2, 3])
        self.assertEqual(p["stale"], ["ter/MI_a.uasset"])
        res = cooked.export(p)
        self.assertEqual((res["copied"], res["skipped"], res["failed"]), (7, 0, []))
        self.assertTrue((self.output / "001_hero/ter/T_a.ubulk").is_file())
        self.assertEqual(cooked.export(p)["skipped"], 7)

    def test_game_paths_and_missing(self):
        p = cooked.plan(self.project, assets=["/Game/Characters/Player/001_hero/ter/MI_a", "nothing.uasset"])
        self.assertEqual(p["assets"][0]["asset"], "ter/MI_a.uasset")
        self.assertEqual(p["missing"], ["nothing.uasset"])
        with self.assertRaises(ValueError):
            cooked.plan(self.project, assets=["/Game/Elsewhere/X"])

    def test_output_inside_source_refused(self):
        with self.assertRaises(ValueError):
            cooked.plan(self.project, output=str(self.source / "sub"))


if __name__ == "__main__":
    unittest.main()
