"""Server state shared by the tools: the editor link and the editor's log file."""
from __future__ import annotations

import time
from pathlib import Path

from . import logs
from .editor import EditorCallError, EditorLink, EditorUnavailable
from .mcp_stdio import ToolError


def _trim(text: str, limit: int = 6000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit // 2] + "\n...\n" + text[-limit // 2:]


class App:
    def __init__(self, project_dir: str | Path, timeout: float = 60.0):
        self.project_dir = Path(project_dir)
        self.link = EditorLink(self.project_dir, default_timeout=timeout)
        self._log_for_node: dict[str, Path] = {}
        self._editor_pid: dict[str, int] = {}
        self._log_offset: int | None = None

    # ---------------------------------------------------------------- editor calls
    def call(self, func: str, args: dict | None = None, timeout: float | None = None):
        try:
            reply = self.link.call(func, args, timeout)
        except EditorUnavailable as e:
            raise ToolError(str(e))
        except EditorCallError as e:
            d = e.details or {}
            parts = [str(e)]
            for key in ("stdout", "stderr"):
                if d.get(key):
                    parts.append(f"--- {key} ---\n" + _trim(d[key]))
            if d.get("traceback"):
                parts.append("--- traceback ---\n" + _trim(d["traceback"], 4000))
            if d.get("editor_output"):
                parts.append("--- editor output ---\n" + _trim(d["editor_output"]))
            raise ToolError("\n".join(parts))
        return reply["result"]

    # ---------------------------------------------------------------- log file
    def log_path(self) -> Path:
        node = self.link.last_node
        if node is not None and node.node_id in self._log_for_node:
            return self._log_for_node[node.node_id]
        info = self.call("log_marker")
        node = self.link.last_node
        candidates = logs.candidate_logs(self.project_dir, info["project_name"], info["engine_version"],
                                         [str(Path(info["project_log_dir"]) / f"{info['project_name']}.log")])
        found = logs.find_marker(candidates, info["marker"])
        if found is None:
            raise ToolError("could not find the editor's log file; looked in: " + ", ".join(map(str, candidates)))
        if node is not None:
            self._log_for_node[node.node_id] = found
            self._editor_pid[node.node_id] = int(info["pid"])
        return found

    def editor_pid(self) -> int:
        self.log_path()
        node = self.link.last_node
        if node is None or node.node_id not in self._editor_pid:
            info = self.call("log_marker")
            return int(info["pid"])
        return self._editor_pid[node.node_id]

    def with_log(self, fn, wait: float = 0.6, max_lines: int = 200):
        """Runs fn() and returns (result, log lines the editor wrote meanwhile)."""
        try:
            path = self.log_path()
            start = logs.file_size(path)
        except ToolError:
            path, start = None, 0
        result = fn()
        lines: list[str] = []
        if path is not None:
            time.sleep(wait)  # the editor flushes its log asynchronously
            lines, _ = logs.read_from(path, start)
            lines = [ln for ln in lines if "ue-mcp-marker-" not in ln]
            if len(lines) > max_lines:
                lines = [f"... {len(lines) - max_lines} earlier lines omitted ..."] + lines[-max_lines:]
        return result, lines

    def take_log_offset(self, path: Path) -> int | None:
        old = self._log_offset
        self._log_offset = logs.file_size(path)
        return old
