"""Exports selected cooked assets the way the HT Cooked Asset Exporter window does.

Ported from SHTCookedAssetExporter.cpp (HTBlueprintToggleTool 1.6.x) so it runs without the
editor: the cooked source folder maps to a /Game path through its "/Content/" part; each asset's
.uasset/.uexp/.ubulk/.uptnl files are copied to <output>/<last folder of the source> (or
<output> when it already ends with that folder) keeping their relative paths. The window's
saved folders and per-source selections are read from the project's
Saved/Config/WindowsEditor/EditorPerProjectUserSettings.ini, where the selection key is
"SelectedAssets_%08X" of UE's GetTypeHash(lower-case source folder).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SECTION = "HTBlueprintToggleTool.CookedAssetExporter"
SIDECARS = (".uasset", ".uexp", ".ubulk", ".uptnl")
DEFAULT_PACKAGER = r"D:\NTE Mod Packager\NTE Mod Packager.exe"  # the path the plugin launches

_CRC_TABLE = None


def _crc_table():
    global _CRC_TABLE
    if _CRC_TABLE is None:
        poly = 0x04C11DB7
        table = []
        for i in range(256):
            c = i << 24
            for _ in range(8):
                c = ((c << 1) ^ poly) if c & 0x80000000 else (c << 1)
            table.append(c & 0xFFFFFFFF)
        _CRC_TABLE = table
    return _CRC_TABLE


def ue_string_hash(text: str) -> int:
    """GetTypeHash(const FString&) == FCrc::Strihash_DEPRECATED (UE 5.6, wide chars)."""
    table = _crc_table()
    h = 0
    for ch in text:
        code = ord(ch.upper()) & 0xFFFF
        h = ((h >> 8) & 0x00FFFFFF) ^ table[(h ^ (code & 0xFF)) & 0xFF]
        h = ((h >> 8) & 0x00FFFFFF) ^ table[(h ^ (code >> 8)) & 0xFF]
    return h


def normalize_dir(path: str | Path) -> str:
    return os.path.abspath(str(path)).replace("\\", "/").rstrip("/")


def selection_key(source_dir: str) -> str:
    return "SelectedAssets_%08X" % ue_string_hash(normalize_dir(source_dir).lower())


def read_settings(project_dir: Path) -> dict:
    ini = project_dir / "Saved" / "Config" / "WindowsEditor" / "EditorPerProjectUserSettings.ini"
    out: dict = {"ini": str(ini), "values": {}, "lists": {}}
    if not ini.is_file():
        return out
    in_section = False
    for raw in ini.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_section = line[1:-1] == SECTION
            continue
        if not in_section or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.lstrip("+-.!")
        if key.startswith("SelectedAssets_"):
            out["lists"].setdefault(key, []).append(value)
        else:
            out["values"][key] = value
    return out


def game_path_of(source_dir: str) -> str | None:
    marker = "/Content/"
    std = normalize_dir(source_dir) + "/"
    i = std.lower().find(marker.lower())
    if i < 0:
        return None
    rel = std[i + len(marker):].rstrip("/")
    return "/Game/" + rel if rel else None


def export_root(source_dir: str, output_dir: str) -> str:
    name = os.path.basename(normalize_dir(source_dir))
    out = normalize_dir(output_dir)
    return out if os.path.basename(out).lower() == name.lower() else out + "/" + name


def _relative_of(asset: str, game_path: str | None) -> str:
    a = asset.strip().replace("\\", "/")
    if a.startswith("/Game/"):
        pkg = a.split(".", 1)[0] if "." in a.rsplit("/", 1)[-1] else a
        if not game_path or not pkg.lower().startswith(game_path.lower() + "/"):
            raise ValueError(f"{asset} is not under the cooked source's {game_path}")
        return pkg[len(game_path) + 1:] + ".uasset"
    return a if a.lower().endswith(".uasset") else a + ".uasset"


def plan(project_dir: Path, source: str | None = None, output: str | None = None,
         assets: list[str] | None = None) -> dict:
    settings = read_settings(project_dir)
    source = source or settings["values"].get("CookedSourceDirectory")
    output = output or settings["values"].get("OutputDirectory")
    if not source:
        raise ValueError("no cooked source folder given and none saved by the exporter window")
    if not output:
        raise ValueError("no output folder given and none saved by the exporter window")
    src = normalize_dir(source)
    if not os.path.isdir(src):
        raise ValueError(f"cooked source folder does not exist: {src} (cook the content first)")
    root = export_root(src, output)
    if root.lower() == src.lower() or root.lower().startswith(src.lower() + "/"):
        raise ValueError("the output folder cannot be the cooked source folder or inside it")
    game_path = game_path_of(src)
    key = selection_key(src)
    selection_source = "given"
    if assets is None:
        assets = settings["lists"].get(key, [])
        selection_source = f"saved selection {key}"
    rows = []
    for a in assets:
        rel = _relative_of(a, game_path)
        base = os.path.splitext(src + "/" + rel)[0]
        files = [base + ext for ext in SIDECARS if os.path.isfile(base + ext)]
        row = {"asset": rel, "files": files}
        if game_path:
            uasset = project_dir / "Content" / (game_path[len("/Game/"):] + "/" + rel)
            if uasset.is_file() and files:
                row["stale"] = os.path.getmtime(files[0]) < uasset.stat().st_mtime
        rows.append(row)
    return {"source": src, "output_root": root, "game_path": game_path, "selection": selection_source,
            "assets": rows, "missing": [r["asset"] for r in rows if not r["files"]],
            "stale": [r["asset"] for r in rows if r.get("stale")]}


def export(p: dict, overwrite: bool = False) -> dict:
    copied, skipped, failed = 0, 0, []
    src = p["source"] + "/"
    for row in p["assets"]:
        for f in row["files"]:
            rel = f[len(src):]
            dest = p["output_root"] + "/" + rel
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest):
                    if not overwrite:
                        skipped += 1
                        continue
                    os.remove(dest)
                shutil.copy2(f, dest)
                copied += 1
            except OSError as e:
                failed.append(f"{rel}: {e}")
    return {"copied": copied, "skipped": skipped, "failed": failed}


def launch_packager(exe: str) -> int:
    if not os.path.isfile(exe):
        raise ValueError(f"packager not found: {exe}")
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True)
    return proc.pid
