import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_editor import TEST_SETTINGS, FakeEditor  # noqa: E402
from ue_mcp.editor import EditorCallError, EditorLink, EditorUnavailable  # noqa: E402
from ue_mcp.machine_lock import LockTimeout, MachineLock  # noqa: E402
from ue_mcp.remote_exec import Broadcast, CommandChannel, RemoteExecutionError, Settings, new_node_id  # noqa: E402


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "Proj"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def link(self, **kw):
        return EditorLink(self.project, settings=TEST_SETTINGS, work_dir=Path(self.tmp.name) / "work", **kw)

    def test_call_roundtrip_with_split_large_reply(self):
        with FakeEditor(str(self.project).replace("\\", "/") + "/") as fake:
            reply = self.link().call("status", {"a": 1})
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["result"], {"echo": "status", "args": {"a": 1}})
        self.assertEqual(len(fake.commands), 1)
        self.assertLess(len(fake.commands[0].encode()), 6000)

    def test_picks_editor_by_project_root(self):
        other = Path(self.tmp.name) / "Other"
        with FakeEditor(str(other) + "/", reply=lambda r: {"ok": True, "result": "wrong"}), \
                FakeEditor(str(self.project) + "/", reply=lambda r: {"ok": True, "result": "right"}):
            self.assertEqual(self.link().call("x")["result"], "right")

    def test_no_editor(self):
        with FakeEditor(str(Path(self.tmp.name) / "Other") + "/"):
            with self.assertRaises(EditorUnavailable) as cm:
                self.link(discover_timeout=1.0).call("x")
        self.assertIn("Other", str(cm.exception))

    def test_editor_side_error_is_reported(self):
        with FakeEditor(str(self.project) + "/", reply=lambda r: {"ok": False, "error": "boom", "traceback": "tb"}):
            with self.assertRaises(EditorCallError) as cm:
                self.link().call("x")
        self.assertEqual(str(cm.exception), "boom")
        self.assertEqual(cm.exception.details["traceback"], "tb")

    def test_timeout(self):
        with FakeEditor(str(self.project) + "/", delay=3.0):
            with self.assertRaises(EditorCallError) as cm:
                self.link().call("x", timeout=1.0)
        self.assertIn("busy", str(cm.exception))

    def test_long_command_refused(self):
        with FakeEditor(str(self.project) + "/") as fake:
            with Broadcast(TEST_SETTINGS, new_node_id()) as bc:
                node = bc.discover(2.0, want=lambda n: True)[0]
                with CommandChannel(bc, node.node_id) as ch:
                    with self.assertRaises(RemoteExecutionError):
                        ch.run("x" * 9000)

    def test_two_sessions_queue(self):
        """Two links calling at once both succeed (the machine lock serialises them)."""
        results = []
        with FakeEditor(str(self.project) + "/", delay=0.3):
            def worker():
                results.append(self.link().call("x")["ok"])
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(results, [True, True, True])


class SettingsTests(unittest.TestCase):
    def test_reads_project_ini(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "Config"
            cfg.mkdir()
            (cfg / "DefaultEngine.ini").write_text(
                "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]\nbRemoteExecution=True\n"
                "RemoteExecutionMulticastGroupEndpoint=239.0.0.2:7000\nRemoteExecutionMulticastBindAddress=0.0.0.0\n"
                "RemoteExecutionMulticastTtl=1\n+SomeArray=(a=1)\n", encoding="utf-8")
            s = Settings.from_project(d)
        self.assertEqual((s.enabled, s.group, s.bind_address, s.ttl), (True, ("239.0.0.2", 7000), "0.0.0.0", 1))


class LockTests(unittest.TestCase):
    def test_second_holder_waits(self):
        name = "ue-mcp-test-" + new_node_id()
        a, b = MachineLock(name), MachineLock(name)
        a.acquire(1)
        try:
            got = []

            def other():
                try:
                    b.acquire(0.3)
                    got.append(True)
                    b.release()
                except LockTimeout:
                    got.append(False)
            t = threading.Thread(target=other)
            t.start()
            t.join()
            self.assertEqual(got, [False])
        finally:
            a.release()


if __name__ == "__main__":
    unittest.main()
