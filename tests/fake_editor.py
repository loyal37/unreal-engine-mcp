"""A stand-in for the editor side of remote execution, for offline tests.

It answers discovery like the editor, connects back on open_connection, and runs the
server's bootstrap by writing the reply file itself.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time

from ue_mcp.remote_exec import Settings, decode_message, encode_message, new_node_id

TEST_SETTINGS = Settings(enabled=True, group=("239.0.0.1", 16766), bind_address="0.0.0.0", ttl=0)


class FakeEditor:
    def __init__(self, project_root: str, settings: Settings = TEST_SETTINGS, reply=None, split_reply: bool = True,
                 delay: float = 0.0):
        self.settings = settings
        self.node_id = new_node_id()
        self.project_root = project_root
        self.reply = reply or (lambda req: {"ok": True, "result": {"echo": req["func"], "args": req["args"]}})
        self.split_reply = split_reply
        self.delay = delay
        self.commands: list[str] = []
        self._stop = threading.Event()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((settings.bind_address, settings.group[1]))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, settings.ttl)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(settings.bind_address))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(settings.group[0]) + socket.inet_aton(settings.bind_address))
        s.settimeout(0.05)
        self.udp = s
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self.thread.join(2)
        self.udp.close()

    def _send(self, type_, dest, data=None):
        self.udp.sendto(encode_message(type_, self.node_id, dest, data), self.settings.group)

    def _run(self):
        while not self._stop.is_set():
            try:
                raw = self.udp.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            msg = decode_message(raw)
            if not msg or msg["source"] == self.node_id:
                continue
            if msg.get("dest") and msg["dest"] != self.node_id:
                continue
            if msg["type"] == "ping":
                self._send("pong", msg["source"], {"user": "test", "machine": "test", "engine_version": "5.6.1",
                                                   "engine_root": "X:/UE/", "project_root": self.project_root,
                                                   "project_name": "Fake"})
            elif msg["type"] == "open_connection":
                threading.Thread(target=self._serve, args=(msg["source"], msg["data"]), daemon=True).start()

    def _serve(self, client_id, data):
        conn = socket.create_connection((data["command_ip"], data["command_port"]), timeout=5)
        try:
            raw = conn.recv(self.settings.receive_buffer)  # one Recv, like the editor
            msg = decode_message(raw)
            if not msg or msg["type"] != "command":
                return
            command = msg["data"]["command"]
            self.commands.append(command)
            req_path = re.search(r"_uem\.handle\(r'([^']+)', r'([^']+)'\)", command)
            result_text = "None"
            if req_path:
                with open(req_path.group(1), encoding="utf-8") as f:
                    req = json.load(f)
                if self.delay:
                    time.sleep(self.delay)
                with open(req_path.group(2), "w", encoding="utf-8") as f:
                    json.dump(self.reply(req), f)
            payload = encode_message("command_result", self.node_id, client_id,
                                     {"success": True, "command": command, "result": result_text,
                                      "output": [{"type": "Info", "output": "x" * 70000}]})
            if self.split_reply:  # arrive in pieces, like a large reply over TCP
                for i in range(0, len(payload), 3000):
                    conn.sendall(payload[i:i + 3000])
                    time.sleep(0.001)
            else:
                conn.sendall(payload)
            time.sleep(0.2)
        finally:
            conn.close()
