"""Talks to one running editor: finds it, runs helper calls, returns their results.

A call writes its request to a JSON file, sends the editor a short bootstrap command
(well below the editor's 8 KB receive buffer) that imports the editor-side helper from
this repository and runs the request, and reads the reply from another JSON file. Only
the bootstrap and a tiny status travel over the socket.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from .machine_lock import LockTimeout, MachineLock
from .remote_exec import (MODE_EXEC_FILE, Broadcast, CommandChannel, RemoteExecutionError, RemoteNode,
                          Settings, new_node_id, normalize_dir)

HELPER_PACKAGE = "ue_mcp_editor"
EDITOR_SIDE_DIR = Path(__file__).resolve().parent / "editor_side"

BOOTSTRAP = """import sys
_d = r'{helper_dir}'
if _d not in sys.path:
    sys.path.insert(0, _d)
import {package} as _uem
_uem = _uem.ensure_current('{helper_hash}')
_uem.handle(r'{request}', r'{response}')
"""


class EditorUnavailable(Exception):
    """No editor of the configured project answers."""


class EditorCallError(Exception):
    """The editor ran the call and reported an error."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def helper_hash() -> str:
    h = hashlib.sha1()
    root = EDITOR_SIDE_DIR / HELPER_PACKAGE
    for path in sorted(root.rglob("*.py")):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


class EditorLink:
    def __init__(self, project_dir: str | Path, settings: Settings | None = None,
                 work_dir: str | Path | None = None, default_timeout: float = 60.0,
                 discover_timeout: float = 2.0, lock_timeout: float = 120.0):
        self.project_dir = Path(project_dir)
        self.settings = settings or Settings.from_project(self.project_dir)
        self.node_id = new_node_id()
        base = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "ue-mcp"
        self.work_dir = base / f"calls-{os.getpid()}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout = default_timeout
        self.discover_timeout = discover_timeout
        self.lock_timeout = lock_timeout
        self._seq = 0
        self.last_node: RemoteNode | None = None

    # ------------------------------------------------------------------ discovery
    def discover(self, timeout: float | None = None) -> list[RemoteNode]:
        with Broadcast(self.settings, self.node_id) as bc:
            return bc.discover(timeout or self.discover_timeout)

    def _find_target(self, bc: Broadcast) -> RemoteNode:
        nodes = bc.discover(self.discover_timeout, want=lambda n: n.matches_project(self.project_dir))
        for node in nodes:
            if node.matches_project(self.project_dir):
                self.last_node = node
                return node
        others = ", ".join(f"{n.info.get('project_name') or '(no project)'} at {n.project_root}" for n in nodes) or "none"
        hint = ""
        if self.settings.enabled is False:
            hint = " Remote execution is turned off in this project's settings."
        raise EditorUnavailable(
            f"no running editor has the project {normalize_dir(self.project_dir)} open "
            f"(editors that answered: {others}).{hint}")

    # ------------------------------------------------------------------ calls
    def call(self, func: str, args: dict | None = None, timeout: float | None = None) -> dict:
        """Runs `func` in the editor-side helper. Returns the reply dict
        (`result`, plus `stdout`/`log` when the helper captured any)."""
        timeout = float(timeout or self.default_timeout)
        self._seq += 1
        stem = f"{int(time.time() * 1000)}-{self._seq}"
        req_path = self.work_dir / f"{stem}.req.json"
        resp_path = self.work_dir / f"{stem}.resp.json"
        request = {
            "id": stem,
            "func": func,
            "args": args or {},
            # Wall clock: the helper refuses to start a request that waited too long in the
            # editor's queue, so a call we gave up on never runs later by surprise.
            "deadline": time.time() + timeout,
            "work_dir": str(self.work_dir),
        }
        req_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        command = BOOTSTRAP.format(helper_dir=str(EDITOR_SIDE_DIR), package=HELPER_PACKAGE,
                                   helper_hash=helper_hash(), request=str(req_path), response=str(resp_path))
        raw: dict = {}
        try:
            with Broadcast(self.settings, self.node_id) as bc:
                node = self._find_target(bc)
                lock = MachineLock("ue-mcp-" + node.node_id)
                try:
                    with lock.hold(self.lock_timeout):
                        with CommandChannel(bc, node.node_id) as channel:
                            raw = channel.run(command, MODE_EXEC_FILE, unattended=True, timeout=timeout)
                except LockTimeout:
                    raise EditorCallError(
                        f"another session kept the editor busy for over {self.lock_timeout:.0f} s; try again")
                except TimeoutError as e:
                    raise EditorCallError(
                        f"{e}. The editor is busy (or a modal dialog is open). The request is dropped "
                        "if it had not started yet; if it had, it is still running.")
                except RemoteExecutionError as e:
                    raise EditorCallError(str(e))
            reply = _read_json(resp_path)
        finally:
            for p in (req_path, resp_path):
                try:
                    p.unlink()
                except OSError:
                    pass
        if reply is None:
            output = "\n".join(str(o.get("output", "")) for o in raw.get("output") or [])
            raise EditorCallError(
                "the editor did not run the helper: " + (raw.get("result") or output or "no reply file"),
                {"editor_result": raw.get("result"), "editor_output": output})
        if not reply.get("ok"):
            raise EditorCallError(reply.get("error") or "unknown editor-side error", reply)
        return reply


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
