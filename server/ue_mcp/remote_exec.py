"""Client for the PythonScriptPlugin remote execution protocol (checked against UE 5.6).

Protocol, as implemented by `PythonScriptRemoteExecution.cpp`:

* Discovery over UDP multicast: we send `ping`, every editor answers `pong` with its
  engine and project roots.
* We listen on a TCP port and send `open_connection` with that port; the editor
  connects back to us. Commands and results then travel over that TCP channel.

Differences from the engine's `remote_execution.py`, all deliberate:

* The multicast socket binds to the project's configured bind address (HT uses
  0.0.0.0; the engine client's default 127.0.0.1 never sees that editor).
* Each channel listens on an ephemeral port, so several clients never fight over 6776.
* Replies are read until the JSON message is complete. The engine client stops as soon
  as one recv() returns less than its buffer size and truncates large replies.
* The editor reads a command with a single Recv() into a buffer of
  `RemoteExecutionReceiveBufferSizeBytes`, so commands must stay short. Callers pass
  data through files instead (see editor.py).
"""
from __future__ import annotations

import configparser
import json
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = "ue_py"

MODE_EXEC_FILE = "ExecuteFile"
MODE_EXEC_STATEMENT = "ExecuteStatement"
MODE_EVAL_STATEMENT = "EvaluateStatement"

SETTINGS_SECTION = "/Script/PythonScriptPlugin.PythonScriptPluginSettings"
MAX_COMMAND_BYTES = 6000  # stay well below the editor's 8 KB receive buffer


class RemoteExecutionError(Exception):
    """Something went wrong talking to the editor."""


@dataclass
class Settings:
    enabled: bool | None = None
    group: tuple[str, int] = ("239.0.0.1", 6766)
    bind_address: str = "127.0.0.1"
    ttl: int = 0
    receive_buffer: int = 8192

    @classmethod
    def from_project(cls, project_dir: str | Path) -> "Settings":
        """Reads the remote execution settings the editor of this project uses."""
        settings = cls()
        for rel in ("Config/DefaultEngine.ini", "Saved/Config/WindowsEditor/Engine.ini"):
            path = Path(project_dir) / rel
            if not path.is_file():
                continue
            parser = configparser.ConfigParser(strict=False, interpolation=None, allow_no_value=True)
            parser.optionxform = str  # keep key case
            try:
                parser.read(path, encoding="utf-8-sig")
            except configparser.Error:
                continue
            if not parser.has_section(SETTINGS_SECTION):
                continue
            sec = parser[SETTINGS_SECTION]
            if "bRemoteExecution" in sec:
                settings.enabled = sec["bRemoteExecution"].strip().lower() == "true"
            if "RemoteExecutionMulticastGroupEndpoint" in sec:
                host, _, port = sec["RemoteExecutionMulticastGroupEndpoint"].strip().rpartition(":")
                settings.group = (host, int(port))
            if "RemoteExecutionMulticastBindAddress" in sec:
                settings.bind_address = sec["RemoteExecutionMulticastBindAddress"].strip()
            if "RemoteExecutionMulticastTtl" in sec:
                settings.ttl = int(sec["RemoteExecutionMulticastTtl"])
            if "RemoteExecutionReceiveBufferSizeBytes" in sec:
                settings.receive_buffer = int(sec["RemoteExecutionReceiveBufferSizeBytes"])
        return settings


@dataclass
class RemoteNode:
    node_id: str
    info: dict = field(default_factory=dict)
    last_seen: float = 0.0

    @property
    def project_root(self) -> str:
        return self.info.get("project_root", "") or ""

    def matches_project(self, project_dir: str | Path) -> bool:
        return normalize_dir(self.project_root) == normalize_dir(project_dir)


def normalize_dir(path: str | Path) -> str:
    text = str(path).replace("\\", "/").rstrip("/")
    return text.casefold()


def encode_message(type_: str, source: str, dest: str | None = None, data=None) -> bytes:
    msg = {"version": PROTOCOL_VERSION, "magic": PROTOCOL_MAGIC, "type": type_, "source": source}
    if dest:
        msg["dest"] = dest
    if data is not None:
        msg["data"] = data
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")


def decode_message(raw: bytes) -> dict | None:
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(msg, dict) or msg.get("version") != PROTOCOL_VERSION or msg.get("magic") != PROTOCOL_MAGIC:
        return None
    if "type" not in msg or "source" not in msg:
        return None
    return msg


def _accepts(msg: dict, node_id: str) -> bool:
    return msg.get("source") != node_id and (not msg.get("dest") or msg.get("dest") == node_id)


class Broadcast:
    """The UDP multicast socket, set up the same way the editor sets up its own."""

    def __init__(self, settings: Settings, node_id: str):
        self.settings = settings
        self.node_id = node_id
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((settings.bind_address, settings.group[1]))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, settings.ttl)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(settings.bind_address))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         socket.inet_aton(settings.group[0]) + socket.inet_aton(settings.bind_address))
        except OSError:
            s.close()
            raise
        s.settimeout(0.05)
        self.sock = s

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, type_: str, dest: str | None = None, data=None) -> None:
        self.sock.sendto(encode_message(type_, self.node_id, dest, data), self.settings.group)

    def receive(self, until: float) -> list[dict]:
        """Every accepted message that arrives before `until` (or until the socket goes quiet)."""
        out = []
        while time.monotonic() < until:
            try:
                raw = self.sock.recv(65535)
            except socket.timeout:
                break
            except OSError:
                break
            msg = decode_message(raw)
            if msg and _accepts(msg, self.node_id):
                out.append(msg)
        return out

    def discover(self, timeout: float = 2.0, want=None) -> list[RemoteNode]:
        """Pings for editors. Returns early once `want(node)` is true for one of them."""
        nodes: dict[str, RemoteNode] = {}
        deadline = time.monotonic() + timeout
        next_ping = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_ping:
                self.send("ping")
                next_ping = now + 0.5
            for msg in self.receive(min(deadline, now + 0.1)):
                if msg.get("type") != "pong":
                    continue
                node = RemoteNode(msg["source"], msg.get("data") or {}, time.monotonic())
                nodes[node.node_id] = node
                if want and want(node):
                    return list(nodes.values())
        return list(nodes.values())


class CommandChannel:
    """One TCP command connection to one editor. Open, run commands, close."""

    def __init__(self, broadcast: Broadcast, remote_node_id: str, connect_timeout: float = 10.0):
        self.broadcast = broadcast
        self.remote_node_id = remote_node_id
        self.sock: socket.socket | None = None
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.25)
            port = listener.getsockname()[1]
            deadline = time.monotonic() + connect_timeout
            next_open = 0.0
            while time.monotonic() < deadline:
                if time.monotonic() >= next_open:
                    broadcast.send("open_connection", remote_node_id, {"command_ip": "127.0.0.1", "command_port": port})
                    next_open = time.monotonic() + 1.0
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                conn.setblocking(True)
                self.sock = conn
                break
        finally:
            listener.close()
        if self.sock is None:
            raise RemoteExecutionError(
                f"the editor did not open a command connection within {connect_timeout:.0f} s "
                "(busy, showing a modal dialog, or remote execution turned off)")

    def close(self) -> None:
        try:
            self.broadcast.send("close_connection", self.remote_node_id)
        except OSError:
            pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def run(self, command: str, exec_mode: str = MODE_EXEC_FILE, unattended: bool = True,
            timeout: float = 60.0) -> dict:
        """Runs one command and returns the editor's `command_result` data."""
        if self.sock is None:
            raise RemoteExecutionError("command channel is closed")
        payload = encode_message("command", self.broadcast.node_id, self.remote_node_id,
                                 {"command": command, "unattended": unattended, "exec_mode": exec_mode})
        if len(payload) > min(MAX_COMMAND_BYTES, self.broadcast.settings.receive_buffer - 512):
            raise RemoteExecutionError(f"command too long for the editor's receive buffer ({len(payload)} bytes)")
        self.sock.sendall(payload)
        return self._receive_result(timeout)

    def _receive_result(self, timeout: float) -> dict:
        assert self.sock is not None
        decoder = json.JSONDecoder()
        buf = b""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no reply from the editor within {timeout:.0f} s")
            self.sock.settimeout(min(remaining, 1.0))
            try:
                part = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not part:
                raise RemoteExecutionError("the editor closed the command connection")
            buf += part
            # The editor sends one JSON object per reply; parse as soon as it is complete.
            text = buf.decode("utf-8", errors="strict") if _is_complete_utf8(buf) else None
            if text is None:
                continue
            try:
                obj, end = decoder.raw_decode(text.lstrip())
            except json.JSONDecodeError:
                continue
            if (isinstance(obj, dict) and obj.get("type") == "command_result"
                    and _accepts(obj, self.broadcast.node_id)):
                return obj.get("data") or {}
            # Something else (should not happen on this channel): drop it and keep reading.
            buf = text.lstrip()[end:].encode("utf-8")


def _is_complete_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def new_node_id() -> str:
    return str(uuid.uuid4()).replace("-", "").upper()
