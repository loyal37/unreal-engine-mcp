import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from ue_mcp import logs  # noqa: E402
from ue_mcp.mcp_stdio import Server, ToolError  # noqa: E402
from ue_mcp.screenshot import encode_png  # noqa: E402


class StdioTests(unittest.TestCase):
    def test_initialize_list_and_call_over_stdio(self):
        with tempfile.TemporaryDirectory() as d:
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "find_assets", "arguments": {"bogus": 1}}},
            ]
            proc = subprocess.run([sys.executable, str(ROOT / "server" / "run_server.py"), "--project", d],
                                  input="\n".join(json.dumps(m) for m in msgs).encode() + b"\n",
                                  capture_output=True, timeout=60)
        replies = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in replies[1]["result"]["tools"]}
        for required in ("run_python", "editor_status", "asset_info", "material_parameters", "material_graph",
                         "material_add_parameter", "instance_set_parameters", "mesh_slots", "save_assets", "output_log"):
            self.assertIn(required, names)
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertIn("unknown argument", replies[2]["result"]["content"][0]["text"])

    def test_tool_error(self):
        srv = Server("t", "0")

        @srv.tool("fail", "fails")
        def fail():
            raise ToolError("nope")

        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fail"}})
        self.assertTrue(r["result"]["isError"])
        self.assertEqual(r["result"]["content"][0]["text"], "nope")


class LogTests(unittest.TestCase):
    def test_read_from_keeps_partial_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.log"
            p.write_bytes(b"one\ntwo\npart")
            lines, off = logs.read_from(p, 0)
            self.assertEqual(lines, ["one", "two"])
            with p.open("ab") as f:
                f.write(b"ial\nthree\n")
            lines, off = logs.read_from(p, off)
            self.assertEqual(lines, ["partial", "three"])

    def test_filter(self):
        lines = ["[2026.11.06-05.16.48:896][382]LogPython: Warning: careful",
                 "[2026.11.06-05.16.48:896][382]LogPython: fine",
                 "[2026.11.06-05.16.48:896][382]LogMaterial: Error: broken"]
        self.assertEqual(logs.filter_lines(lines, min_verbosity="Warning"), [lines[0], lines[2]])
        self.assertEqual(logs.filter_lines(lines, categories=["logpython"]), lines[:2])
        self.assertEqual(logs.filter_lines(lines, contains="BROKEN"), [lines[2]])


class PngTests(unittest.TestCase):
    def test_png_header(self):
        png = encode_png(bytes([0, 0, 255, 0]) * 4, 2, 2)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", png)


if __name__ == "__main__":
    unittest.main()
