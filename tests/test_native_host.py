"""Native messaging/lifetime tests: temporary stores and owned loopback processes only."""
import copy
import io
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
import automation
import native_host
import storage
import integrations
from api import routes

ORIGIN = "chrome-extension://" + "a" * 32 + "/"


def frame(value):
    stream = io.BytesIO()
    native_host.write_message(stream, value)
    return stream.getvalue()


class StubOwner:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")
        return {"ok": True, "state": "on"}

    def status(self):
        self.calls.append("status")
        return {"ok": True, "state": "off"}

    def stop(self):
        self.calls.append("stop")
        return {"ok": True, "state": "off"}


class NativeProtocolTests(unittest.TestCase):
    def test_native_endian_bounded_frames_and_exact_responses(self):
        owner, output = StubOwner(), io.BytesIO()
        source = io.BytesIO(b"".join(frame({"id": str(i), "action": action})
                                   for i, action in enumerate(("status", "start", "stop"))))
        native_host.serve(owner, source, output)
        output.seek(0)
        replies = [native_host.read_message(output) for _ in range(3)]
        self.assertEqual([value["state"] for value in replies], ["off", "on", "off"])
        self.assertTrue(all(set(value) == {"id", "ok", "state"} for value in replies))
        self.assertEqual(owner.calls, ["status", "start", "stop", "stop"])
        self.assertIsNone(native_host.read_message(output))
        self.assertEqual(struct.unpack("=I", frame({"id": "1"})[:4])[0], 10)

    def test_browser_cannot_send_paths_environment_or_commands(self):
        for message in ({"id": "1", "action": "start", "path": "/tmp/evil"},
                        {"id": "1", "action": "start", "env": {"PANTHER_DATA_DIR": "/tmp/evil"}},
                        {"id": "1", "action": "shell"}, {"id": "1", "action": []}):
            owner, output = StubOwner(), io.BytesIO()
            native_host.serve(owner, io.BytesIO(frame(message)), output)
            output.seek(0)
            self.assertFalse(native_host.read_message(output)["ok"])
            self.assertEqual(owner.calls, ["stop"])

    def test_malformed_or_oversized_frames_stop_owned_backend_without_commands(self):
        invalid = [b"\x01", struct.pack("=I", 0), struct.pack("=I", native_host.MAX_FRAME + 1),
                   struct.pack("=I", 2) + b"x", struct.pack("=I", 1) + b"\xff", frame([]),
                   struct.pack("=I", 23) + b'{"id":"1","id":"2"}xxxx']
        for data in invalid:
            with self.subTest(data=data):
                owner = StubOwner()
                with self.assertRaises(native_host.ProtocolError):
                    native_host.serve(owner, io.BytesIO(data), io.BytesIO())
                self.assertEqual(owner.calls, ["stop"])

    def test_partial_stream_reads_are_reassembled(self):
        class Partial(io.BytesIO):
            def read(self, size):
                return super().read(min(size, 1))
        self.assertEqual(native_host.read_message(Partial(frame({"id": "one", "action": "status"}))),
                         {"id": "one", "action": "status"})

    def test_private_config_pins_origin_and_only_local_services(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.json"
            config = {"version": 1, "allowed_origin": ORIGIN, "data_dir": directory,
                      "dashboard_url": "http://localhost:7878", "ollama_endpoint": "http://127.0.0.1:11434/v1",
                      "ollama_model": "local-test"}
            storage.write_json(path, config)
            loaded = native_host.load_config(path, ORIGIN)
            self.assertEqual(loaded["dashboard_url"], "http://127.0.0.1:7878")
            for changes, origin in (({}, ORIGIN.replace("a", "b")), ({"version": True}, ORIGIN),
                                    ({"dashboard_url": "https://remote.example"}, ORIGIN),
                                    ({"ollama_endpoint": "http://user:secret@localhost:11434/v1"}, ORIGIN),
                                    ({"backend_port": 9999}, ORIGIN), ({"command": "echo hi"}, ORIGIN)):
                storage.write_json(path, {**config, **changes})
                with self.assertRaises(ValueError):
                    native_host.load_config(path, origin)
            storage.write_json(path, config)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                native_host.load_config(path, ORIGIN)

    def test_force_stop_targets_only_owned_child_and_confirms_exit(self):
        class Child:
            def __init__(self):
                self.calls, self.exited = [], False

            def poll(self):
                return -15 if self.exited else None

            def wait(self, timeout):
                self.calls.append(("wait", timeout))
                if not self.exited:
                    raise subprocess.TimeoutExpired("owned-child", timeout)
                return -15

            def terminate(self):
                self.calls.append(("terminate",))
                self.exited = True

        owner = native_host.BackendOwner({})
        child = Child()
        owner.child = child
        self.assertEqual(owner.stop(), {"ok": True, "state": "off"})
        self.assertEqual(child.calls, [("wait", native_host.STOP_TIMEOUT), ("terminate",), ("wait", 1)])


class FakeLocalServices(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value):
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        state = self.server.state
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/chat/completions":
            state["model_entered"].set()
            state["release"].wait(8)
            return self.reply({"choices": [{"message": {"content": '[{"title":"A proposal"}]'}}]})
        state["posts"].append(data)
        item = {**data, "id": 1234, "key": "M-1234", "native_status": "backlog"}
        state["item"] = item
        state["post_entered"].set()
        if state.get("hold_post"):
            state["release"].wait(8)
        self.reply({"ok": True, "item": 1234, "created": True})

    def do_GET(self):
        self.reply(self.server.state.get("item") or {"ok": True})


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage, key, value) for key, value in {
            "ROOT": self.root, "RECORDINGS_DIR": self.root / "recordings", "EVENTS_DIR": self.root / "events",
            "INFERENCE_DIR": self.root / "inference", "TICKETS_DIR": self.root / "tickets",
            "MANIFEST_FILE": self.root / "manifest.json", "CONSENT_LOG": self.root / "consent.log",
        }.items()]
        for item in self.patches:
            item.start()
        automation._CANCELLED.clear()
        automation._PAUSED.clear()
        automation._BROWSER_OWNED = False
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeLocalServices)
        self.fake.state = {"posts": [], "post_entered": threading.Event(),
                           "model_entered": threading.Event(), "release": threading.Event()}
        self.fake_thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        self.fake_thread.start()
        self.target = f"http://127.0.0.1:{self.fake.server_port}"
        self.env = patch.dict(os.environ, {"PANTHER_DASHBOARD_URL": self.target})
        self.env.start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.config = {"version": 1, "allowed_origin": ORIGIN, "data_dir": str(self.root),
                       "dashboard_url": self.target, "ollama_endpoint": self.target + "/v1", "ollama_model": "synthetic-only"}
        self.owner = native_host.BackendOwner(self.config, port=self.port)
        self.hosts = []

    def tearDown(self):
        self.fake.state["release"].set()
        self.owner.stop()
        for host in self.hosts:
            if host.poll() is None:
                host.stdin.close()
                host.wait(timeout=25)
            for stream in (host.stdout, host.stderr):
                if stream:
                    stream.close()
        self.fake.shutdown()
        self.fake.server_close()
        self.fake_thread.join()
        self.env.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()
        automation._CANCELLED.clear()
        automation._PAUSED.clear()
        automation._BROWSER_OWNED = False

    def running(self):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    def await_off(self, timeout=4):
        deadline = time.monotonic() + timeout
        while self.running() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(self.running())

    def seed_job(self, name="old"):
        start = time.time() - 5
        policy = automation.admit_policy({"enabled": True, "consent": True, "target_url": self.target,
                                         "use_ai": False, "max_tickets": 5}, start)
        rid = storage.create_recording(tab_id=1, start_time=start, end_time=start + 1, event_count=0,
                                       extension_id="test", session_id=name, automation_policy=policy)
        automation.enqueue(storage.get_recording(rid))
        return rid

    def capture(self, *, use_ai=False, name="current"):
        start = time.time() - 1
        body = {"consent": True, "extensionId": "test", "sessionId": name,
                "startTime": start * 1000, "endTime": time.time() * 1000, "events": [{"type": "click", "target": {"tag": "button", "label": "Refresh"}}],
                "automation": {"enabled": True, "consent": True, "target_url": self.target, "use_ai": use_ai, "max_tickets": 5}}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/extension/record", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Panther-Client": "test"})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.loads(response.read())["id"]

    def test_explicit_start_status_stop_own_only_their_child(self):
        self.assertEqual(self.owner.status(), {"ok": True, "state": "off"})
        self.assertFalse(self.running())
        self.assertEqual(self.owner.start(), {"ok": True, "state": "on"})
        child = self.owner.child
        self.assertTrue(self.running())
        self.assertEqual(self.owner.start()["state"], "on")
        self.assertIs(self.owner.child, child)
        self.assertEqual(self.owner.stop(), {"ok": True, "state": "off"})
        self.assertIsNotNone(child.poll())
        self.await_off()
        self.assertTrue((self.root / "browser-revocation.json").is_file())

    def test_occupied_port_is_never_adopted_stopped_or_revoked(self):
        rid = self.seed_job()
        before = automation._path(rid).read_bytes()
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", self.port))
            foreign.listen()
            result = self.owner.start()
            self.assertFalse(result["ok"])
            self.assertIn("occupied", result["error"])
            self.assertEqual(self.owner.stop()["state"], "off")
            self.assertTrue(self.running())
        self.assertEqual(automation._path(rid).read_bytes(), before)
        self.assertFalse((self.root / "browser-revocation.json").exists())

    def test_new_on_cancels_stale_jobs_before_worker_without_dashboard_calls(self):
        rid = self.seed_job()
        capture = storage.get_recording(rid)
        self.assertTrue(self.owner.start()["ok"])
        self.assertEqual(automation._load(rid)["status"], "cancelled")
        self.assertEqual(self.fake.state["posts"], [])
        self.assertEqual(storage.get_recording(rid), capture)
        self.owner.stop()
        self.assertTrue(self.owner.start()["ok"])
        self.assertEqual(automation._load(rid)["status"], "cancelled")
        self.assertEqual(self.fake.state["posts"], [])

    def test_off_during_inference_does_not_wait_for_model_or_resume_old_job(self):
        self.assertTrue(self.owner.start()["ok"])
        rid = self.capture(use_ai=True)
        self.assertTrue(self.fake.state["model_entered"].wait(3))
        started = time.monotonic()
        self.assertTrue(self.owner.stop()["ok"])
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(automation._load(rid)["status"], "cancelled")
        self.assertEqual(self.fake.state["posts"], [])
        self.assertTrue(self.owner.start()["ok"])
        self.assertEqual(automation._load(rid)["status"], "cancelled")
        self.assertEqual(self.fake.state["posts"], [])

    def test_off_during_post_preserves_inflight_truth_and_revokes_future_work(self):
        self.fake.state["hold_post"] = True
        self.assertTrue(self.owner.start()["ok"])
        rid = self.capture()
        self.assertTrue(self.fake.state["post_entered"].wait(3))
        stopped = []
        stopping = threading.Thread(target=lambda: stopped.append(self.owner.stop()))
        stopping.start()
        self.await_off()
        self.assertTrue((self.root / "browser-revocation.json").exists())
        self.fake.state["release"].set()
        stopping.join(5)
        self.assertFalse(stopping.is_alive())
        self.assertTrue(stopped[0]["ok"])
        job = automation._load(rid)
        self.assertIn(job["status"], {"cancelled", "delivered"})
        self.assertEqual(len(self.fake.state["posts"]), 1)
        self.assertEqual(len(list((self.root / "deliveries").glob("*.json"))), 1)
        self.assertTrue(self.owner.start()["ok"])
        self.assertEqual(len(self.fake.state["posts"]), 1)

    def start_native_process(self):
        code = "import json,sys;sys.path.insert(0,sys.argv[1]);import native_host;native_host.serve(native_host.BackendOwner(json.loads(sys.argv[2]),port=int(sys.argv[3])),sys.stdin.buffer,sys.stdout.buffer)"
        host = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(BACKEND), json.dumps(self.config), str(self.port)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.hosts.append(host)
        native_host.write_message(host.stdin, {"id": "start", "action": "start"})
        self.assertTrue(select.select([host.stdout], [], [], 10)[0])
        self.assertEqual(native_host.read_message(host.stdout), {"id": "start", "ok": True, "state": "on"})
        return host

    def test_native_stdout_is_framed_and_eof_stops_backend(self):
        host = self.start_native_process()
        native_host.write_message(host.stdin, {"id": "status", "action": "status"})
        self.assertEqual(native_host.read_message(host.stdout), {"id": "status", "ok": True, "state": "on"})
        host.stdin.close()
        self.assertEqual(host.wait(timeout=5), 0)
        self.await_off()
        self.assertEqual(host.stdout.read(), b"")

    def test_native_host_kill_drops_lease_and_does_not_leave_listener_or_resume(self):
        host = self.start_native_process()
        host.kill()
        host.wait(timeout=3)
        host.stdin.close()
        self.await_off()
        deadline = time.monotonic() + 3
        while not (self.root / "browser-revocation.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.root / "browser-revocation.json").exists())
        self.assertTrue(self.owner.start()["ok"])

    def test_crashed_owned_child_is_error_not_adoption_or_automatic_restart(self):
        self.assertTrue(self.owner.start()["ok"])
        self.owner.child.kill()
        self.owner.child.wait(timeout=3)
        self.assertEqual(self.owner.status()["state"], "error")
        self.assertFalse(self.running())
        self.assertEqual(self.owner.stop()["state"], "off")

    def test_abrupt_child_death_cannot_resume_browser_jobs_in_cli_mode(self):
        self.assertTrue(self.owner.start()["ok"])
        rid = self.capture(use_ai=True)
        self.assertTrue(self.fake.state["model_entered"].wait(3))
        self.assertTrue(storage.get_recording(rid)["automation"]["browser_session"])
        self.owner.child.kill()
        self.owner.child.wait(timeout=3)
        self.owner.stop()
        self.assertFalse((self.root / "browser-revocation.json").exists())
        with patch.object(automation.tickets, "generate_drafts", side_effect=AssertionError("A dead browser lease cannot run in CLI")):
            self.assertTrue(automation.run_once())
        self.assertEqual(automation._load(rid)["status"], "cancelled")
        self.assertEqual(self.fake.state["posts"], [])

    def test_revocation_marker_fences_older_jobs_even_in_cli_mode(self):
        rid = self.seed_job()
        automation.revoke_browser_work()
        automation._PAUSED.clear()  # Simulate a later standalone process launch.
        with patch.object(automation.tickets, "generate_drafts", side_effect=AssertionError("Revoked job must not run")):
            self.assertTrue(automation.run_once())
        self.assertEqual(automation._load(rid)["status"], "cancelled")
        self.assertEqual(self.fake.state["posts"], [])

    def test_revocation_after_pending_receipt_prevents_outbound_post(self):
        rid = self.seed_job()
        original = storage.write_json
        revoked = []

        def revoke_after_receipt(path, data):
            original(path, data)
            if Path(path).parent.name == "deliveries" and not revoked:
                revoked.append(True)
                automation.revoke_browser_work()

        with patch.object(storage, "write_json", side_effect=revoke_after_receipt):
            self.assertTrue(automation.run_once())
        self.assertTrue(revoked)
        self.assertEqual(self.fake.state["posts"], [])
        self.assertEqual(automation._load(rid)["status"], "cancelled")

    def test_manual_adapter_has_last_moment_browser_power_gate(self):
        automation._BROWSER_OWNED = True
        automation._PAUSED.set()
        with patch.object(integrations, "BEFORE_POST", automation.require_browser_active):
            result = integrations.submit_ticket({"id": "test-capture"}, {"id": "draft", "title": "Proposed change"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.fake.state["posts"], [])

    def route_request(self, *, pause_during_body=False):
        body = json.dumps({"sessionId": "off-request", "extensionId": "test", "consent": True,
                           "startTime": time.time() * 1000, "endTime": time.time() * 1000, "events": []}).encode()
        headers = Message()
        for key, value in {"Host": "127.0.0.1:7890", "Content-Type": "application/json",
                           "Content-Length": str(len(body)), "X-Panther-Client": "test"}.items():
            headers[key] = value

        class Request:
            command, path = "POST", "/api/extension/record"
            def send_response(self, status):
                self.status = status
            def send_header(self, *args):
                pass
            def end_headers(self):
                pass
            def log_error(self, *args):
                pass

        class Body(io.BytesIO):
            def read(self, count):
                if pause_during_body:
                    automation._PAUSED.set()
                return super().read(count)

        request = Request()
        request.headers, request.rfile, request.wfile = headers, Body(body), io.BytesIO()
        routes.handle_request(request)
        return request.status, json.loads(request.wfile.getvalue())

    def test_off_rejects_new_http_admission_and_body_wait_race(self):
        automation._BROWSER_OWNED = True
        automation._PAUSED.set()
        self.assertEqual(self.route_request()[0], 503)
        automation._PAUSED.clear()
        code, result = self.route_request(pause_during_body=True)
        self.assertEqual(code, 400, result)
        self.assertEqual(storage.list_recordings(), [])


if __name__ == "__main__":
    unittest.main()
