"""Reads the running editor's log file from outside the editor.

Which file the editor writes depends on how it was started: launched with a project it
is `<Project>/Saved/Logs/<Project>.log`, launched bare with an auto-load project it is
`%LOCALAPPDATA%/UnrealEngine/<major.minor>/Saved/Logs/<Project>.log`. The editor-side
helper writes a unique marker line and we look for it in the candidates.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

LINE_RE = re.compile(r"^\[(?P<time>[^\]]+)\]\[\s*(?P<frame>\d+)\](?P<category>[A-Za-z0-9_]+): (?:(?P<verbosity>Fatal|Error|Warning|Display|Log|Verbose|VeryVerbose): )?(?P<message>.*)$")


def candidate_logs(project_dir: Path, project_name: str, engine_version: str, extra: list[str] | None = None) -> list[Path]:
    major_minor = ".".join(engine_version.split(".")[:2]) if engine_version else "5.6"
    out = [project_dir / "Saved" / "Logs" / f"{project_name}.log"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "UnrealEngine" / major_minor / "Saved" / "Logs" / f"{project_name}.log")
    for e in extra or []:
        out.append(Path(e))
    seen, unique = set(), []
    for p in out:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def find_marker(paths: list[Path], marker: str, wait: float = 4.0) -> Path | None:
    """Returns the first file whose tail contains `marker` (the editor flushes its log
    asynchronously, so this polls for a few seconds)."""
    deadline = time.monotonic() + wait
    needle = marker.encode("utf-8")
    while True:
        for p in paths:
            try:
                size = p.stat().st_size
                with p.open("rb") as f:
                    f.seek(max(0, size - 256 * 1024))
                    if needle in f.read():
                        return p
            except OSError:
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_from(path: Path, offset: int, max_bytes: int = 2 * 1024 * 1024) -> tuple[list[str], int]:
    """Lines appended since `offset`, and the new offset. Handles a log that was rotated."""
    size = file_size(path)
    if size < offset:
        offset = 0
    start = max(offset, size - max_bytes)
    with path.open("rb") as f:
        f.seek(start)
        data = f.read(size - start)
    text = data.decode("utf-8", errors="replace")
    if start > offset:  # we skipped some; drop the partial first line
        text = text.split("\n", 1)[-1]
    lines = text.splitlines()
    # do not consume a trailing partial line
    if data and not data.endswith(b"\n") and lines:
        partial = lines.pop()
        size -= len(partial.encode("utf-8"))
    return lines, size


def tail(path: Path, lines: int = 200, max_bytes: int = 4 * 1024 * 1024) -> list[str]:
    size = file_size(path)
    start = max(0, size - max_bytes)
    with path.open("rb") as f:
        f.seek(start)
        data = f.read()
    out = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and out:
        out = out[1:]
    return out[-lines:] if lines > 0 else out


def parse(line: str) -> dict:
    m = LINE_RE.match(line)
    if not m:
        return {"category": None, "verbosity": None, "message": line}
    return {"time": m["time"], "frame": int(m["frame"]), "category": m["category"],
            "verbosity": m["verbosity"] or "Log", "message": m["message"]}


VERBOSITY_RANK = {"Fatal": 0, "Error": 1, "Warning": 2, "Display": 3, "Log": 4, "Verbose": 5, "VeryVerbose": 6}


def filter_lines(lines: list[str], contains: str | None = None, categories: list[str] | None = None,
                 min_verbosity: str | None = None, regex: str | None = None) -> list[str]:
    cats = {c.casefold() for c in categories} if categories else None
    max_rank = VERBOSITY_RANK.get(min_verbosity or "", None)
    rx = re.compile(regex) if regex else None
    out = []
    for line in lines:
        if contains and contains.casefold() not in line.casefold():
            continue
        if rx and not rx.search(line):
            continue
        if cats is not None or max_rank is not None:
            p = parse(line)
            if cats is not None and (p["category"] or "").casefold() not in cats:
                continue
            if max_rank is not None and VERBOSITY_RANK.get(p["verbosity"] or "Log", 4) > max_rank:
                continue
        out.append(line)
    return out
