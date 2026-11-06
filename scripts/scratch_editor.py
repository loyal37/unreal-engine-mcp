"""Creates, starts and stops a throwaway editor for testing tools that change assets.

Every editor launch rewrites %LOCALAPPDATA%/UnrealEngine/<ver>/Saved/AutoLoadProject.txt and
moves its project to the top of the recent list in .../Saved/Config/WindowsEditor/
EditorSettings.ini. `start` backs both files up first; `stop` closes the scratch editor (only
it: the process is identified by PID and by its project) and restores both files.

    python scripts/scratch_editor.py create  --dir <scratch> [--plugins <folder> ...]
    python scripts/scratch_editor.py start   --dir <scratch>
    python scripts/scratch_editor.py stop    --dir <scratch>
    python scripts/scratch_editor.py restore --dir <scratch>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ENGINE = Path(os.environ.get("UE_ROOT", r"D:\ue\UE_5.6"))
EDITOR_EXE = ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
LOCAL = Path(os.environ["LOCALAPPDATA"]) / "UnrealEngine" / "5.6" / "Saved"
AUTOLOAD = LOCAL / "AutoLoadProject.txt"
EDITOR_SETTINGS = LOCAL / "Config" / "WindowsEditor" / "EditorSettings.ini"
PROJECT_NAME = "McpTest"

ENGINE_INI = """[/Script/PythonScriptPlugin.PythonScriptPluginSettings]
bRemoteExecution=True
RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
RemoteExecutionMulticastBindAddress=0.0.0.0
RemoteExecutionMulticastTtl=0
"""
EDITOR_USER_INI = """[/Script/UnrealEd.EditorPerformanceSettings]
bThrottleCPUWhenNotForeground=False
"""


def uproject(d: Path) -> Path:
    return d / f"{PROJECT_NAME}.uproject"


def create(d: Path, plugins: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "Config").mkdir(exist_ok=True)
    (d / "Content").mkdir(exist_ok=True)
    desc = {"FileVersion": 3, "EngineAssociation": "5.6", "Category": "", "Description": "ue-mcp scratch project",
            "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": True},
                        {"Name": "EditorScriptingUtilities", "Enabled": True}]}
    if plugins:
        desc["AdditionalPluginDirectories"] = [str(Path(p).resolve()) for p in plugins]
        for p in plugins:
            for up in Path(p).glob("*/*.uplugin"):
                desc["Plugins"].append({"Name": up.stem, "Enabled": True})
    uproject(d).write_text(json.dumps(desc, indent=1), encoding="utf-8")
    (d / "Config" / "DefaultEngine.ini").write_text(ENGINE_INI, encoding="utf-8")
    (d / "Config" / "DefaultEditorPerProjectUserSettings.ini").write_text(EDITOR_USER_INI, encoding="utf-8")
    print(f"created {uproject(d)}")


def backup_dir(d: Path) -> Path:
    return d / "_editor_settings_backup"


def backup(d: Path) -> None:
    b = backup_dir(d)
    if b.exists():
        print(f"backup already exists in {b}; keeping it (restore first if it is stale)")
        return
    b.mkdir()
    for f in (AUTOLOAD, EDITOR_SETTINGS):
        if f.exists():
            shutil.copy2(f, b / f.name)
    print(f"backed up {AUTOLOAD.name} and {EDITOR_SETTINGS.name} to {b}")


def restore(d: Path) -> None:
    b = backup_dir(d)
    if not b.exists():
        print("nothing to restore")
        return
    for f in (AUTOLOAD, EDITOR_SETTINGS):
        src = b / f.name
        if src.exists():
            shutil.copy2(src, f)
    shutil.rmtree(b)
    print("restored; AutoLoadProject.txt =", AUTOLOAD.read_text(encoding="utf-8", errors="replace").strip())


def start(d: Path, wait: float) -> None:
    pid_file = d / "editor.pid"
    if pid_file.exists() and _alive(int(pid_file.read_text())):
        print("scratch editor already running, pid", pid_file.read_text())
        return
    backup(d)
    proc = subprocess.Popen([str(EDITOR_EXE), str(uproject(d)), "-nosplash", "-unattended", "-nopause",
                             "-NoLiveCoding"], cwd=str(d))
    pid_file.write_text(str(proc.pid))
    print("started scratch editor, pid", proc.pid)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    from ue_mcp.editor import EditorLink

    link = EditorLink(d, discover_timeout=1.0)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not _alive(proc.pid):
            raise SystemExit("the scratch editor exited during startup; see its Saved/Logs")
        if any(n.matches_project(d) for n in link.discover(1.0)):
            print(f"scratch editor answers remote execution after {wait - (deadline - time.time()):.0f} s")
            return
        time.sleep(2)
    raise SystemExit("scratch editor did not answer remote execution in time")


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def stop(d: Path) -> None:
    pid_file = d / "editor.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text())
        if _alive(pid):
            # Only this PID, which this script started for the scratch project.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            for _ in range(60):
                if not _alive(pid):
                    break
                time.sleep(1)
        pid_file.unlink()
        print("stopped scratch editor", pid)
    restore(d)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["create", "start", "stop", "restore"])
    p.add_argument("--dir", required=True)
    p.add_argument("--plugins", action="append", default=[])
    p.add_argument("--wait", type=float, default=600)
    a = p.parse_args()
    d = Path(a.dir)
    {"create": lambda: create(d, a.plugins), "start": lambda: start(d, a.wait), "stop": lambda: stop(d),
     "restore": lambda: restore(d)}[a.command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
