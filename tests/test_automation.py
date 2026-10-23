"""Automatic delivery qualification: private temporary data and fake loopback HTTP only."""
import copy
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import automation
import destinations
import github
import integrations
import storage
import tickets
from server import Handler


class FakeDashboard(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        state = self.server.state
        state["gets"].append(self.path)
        if self.path == "/healthz":
            return self.reply(200, {"ok": True})
        if state.get("readback_unavailable"):
            return self.reply(503, {"error": "unavailable"})
        key = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("key", [""])[0]
        item = next((row for row in state["items"].values() if row["key"] == key), None)
        self.reply(200, item or {"error": "missing"})

    def do_POST(self):
        state = self.server.state
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["posts"].append(payload)
        if state.get("refuse"):
            return self.reply(200, {"ok": False, "error": "intake paused"})
        created = payload["seed_key"] not in state["items"]
        number = 1200 + len(state["items"])
        item = state["items"].setdefault(payload["seed_key"], {
            **payload, "id": number, "key": f"M-{number}", "native_status": "backlog"})
        if state.pop("drop_after_write", False):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.reply(200, {"ok": True, "item": item["id"], "created": created})


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class AutomationTests(unittest.TestCase):
    def setUp(self):
        automation.stop_worker()
        automation._CANCELLED.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage, key, value) for key, value in {
            "ROOT": self.root, "RECORDINGS_DIR": self.root / "recordings",
            "EVENTS_DIR": self.root / "events", "INFERENCE_DIR": self.root / "inference",
            "TICKETS_DIR": self.root / "tickets", "CONSENT_LOG": self.root / "consent.log",
            "MANIFEST_FILE": self.root / "manifest.json",
        }.items()]
        for item in self.patches:
            item.start()
        self.dashboard = ThreadingHTTPServer(("127.0.0.1", 0), FakeDashboard)
        self.dashboard.state = {"gets": [], "posts": [], "items": {}}
        self.dashboard_thread = threading.Thread(target=self.dashboard.serve_forever, daemon=True)
        self.dashboard_thread.start()
        self.target = f"http://127.0.0.1:{self.dashboard.server_port}"
        self.env = patch.dict(os.environ, {"PANTHER_DASHBOARD_URL": self.target})
        self.env.start()
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.backend_thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.backend_thread.start()
        self.origin = f"http://127.0.0.1:{self.backend.server_port}"
        self.start = time.time() - 5
        self.policy = {"enabled": True, "consent": True, "target_url": self.target,
                       "use_ai": False, "max_tickets": 5}
        self.capture = {"sessionId": "automation-test", "extensionId": "test-extension", "consent": True,
                        "tabId": 1, "startTime": self.start * 1000, "endTime": (self.start + 2) * 1000,
                        "url": "https://example.test/tasks?token=private", "title": "Synthetic tasks",
                        "goal": "Keep the selected task after refresh", "automation": self.policy,
                        "events": [{"type": "navigation", "ts": self.start * 1000,
                                    "url": "https://example.test/tasks"},
                                   {"type": "click", "ts": (self.start + 1) * 1000,
                                    "target": {"tag": "button", "label": "Refresh"}}]}

    def tearDown(self):
        automation.stop_worker(timeout=5)
        self.backend.shutdown()
        self.backend.server_close()
        self.backend_thread.join()
        self.dashboard.shutdown()
        self.dashboard.server_close()
        self.dashboard_thread.join()
        self.env.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()
        automation._CANCELLED.clear()

    def request(self, path, body=None, method=None):
        req = urllib.request.Request(self.origin + "/api/extension/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method or ("POST" if body is not None else "GET"),
            headers={"Content-Type": "application/json", "X-Panther-Client": "test"})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def record(self, **changes):
        code, result = self.request("record", {**self.capture, **changes})
        self.assertEqual(code, 201, result)
        return result["id"]

    def job(self, rid):
        return automation.status(rid)

    def run_job(self, rid):
        self.assertTrue(automation.run_once())
        return self.job(rid)

    def generated(self, rid, count=2):
        entry = storage.get_recording(rid)
        data = tickets.generate_drafts(entry, json.loads(Path(entry["events_path"]).read_text()))
        data["tickets"] = [dict(copy.deepcopy(data["tickets"][0]), id=f"observed-{index}") for index in range(count)]
        return data

    def test_complete_http_pipeline_runs_without_manual_generation_or_send(self):
        with patch.object(tickets, "_ollama_decompose", side_effect=AssertionError("Evidence mode must not call AI")):
            automation.start_worker()
            rid = self.record()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                code, state = self.request(f"automation/{rid}")
                if state["status"] in automation.TERMINAL:
                    break
                time.sleep(0.01)
        self.assertEqual(code, 200)
        self.assertEqual(state["status"], "delivered", state)
        self.assertEqual((state["ticket_count"], state["delivered_count"]), (1, 1))
        self.assertFalse(state["execution_authorized"])
        self.assertTrue(state["deliveries"][0]["verified"])
        self.assertIsNotNone(state["deliveries"][0]["verified_at"])
        payload = self.dashboard.state["posts"][0]
        self.assertEqual(payload["status"], "backlog")
        self.assertIn("no-auto", payload["tags"])
        self.assertNotIn("auto", payload["tags"])
        self.assertIn(integrations.FENCE, payload["detail"])
        self.assertIn("no AI inference", payload["detail"])
        self.assertNotIn("private", json.dumps(payload))
        self.assertEqual(len(self.dashboard.state["items"]), 1)
        self.assertIn("seen=0", self.dashboard.state["gets"][-1])
        for private in ("frozen", "events_path", "steps_to_reproduce", "payload_hash", "goal"):
            self.assertNotIn(private, state)

    def test_manual_capture_does_not_admit_job(self):
        for policy in (None, {"enabled": False}):
            rid = self.record(automation=policy, sessionId=str(policy))
            self.assertEqual(self.job(rid)["status"], "disabled")
            self.assertFalse(automation._path(rid).exists())
        self.assertFalse(automation.run_once())
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_policy_requires_exact_consent_target_types_bounds_and_recent_start(self):
        for policy in ({}, {**self.policy, "consent": False}, {**self.policy, "use_ai": "false"},
                       {**self.policy, "max_tickets": True}, {**self.policy, "max_tickets": 6},
                       {**self.policy, "target_url": self.target + "/"}, {**self.policy, "execution": True}):
            with self.subTest(policy=policy):
                code, _ = self.request("record", {**self.capture, "automation": policy})
                self.assertEqual(code, 400)
        for start in (time.time() - 86401, time.time() + 301):
            code, _ = self.request("record", {**self.capture, "startTime": start * 1000, "endTime": start * 1000})
            self.assertEqual(code, 400)
        self.assertEqual(storage.list_recordings(), [])

    def test_duplicate_upload_cannot_renew_or_change_original_policy(self):
        rid = self.record()
        before = storage.get_recording(rid)["automation"]
        with patch.object(automation.time, "time", return_value=time.time() + 100):
            code, result = self.request("record", self.capture)
        self.assertEqual(code, 200, result)
        self.assertEqual(storage.get_recording(rid)["automation"], before)
        self.assertEqual(before["expires_at"], self.capture["startTime"] / 1000 + 86400)
        code, _ = self.request("record", {**self.capture, "automation": {**self.policy, "use_ai": True}})
        self.assertEqual(code, 400)
        self.assertEqual(automation._load(rid)["attempts"], 0)

    def test_active_job_blocks_manual_generation_edit_and_send(self):
        rid = self.record()
        for path, body in ((f"tickets/{rid}", {}), (f"tickets/{rid}/review", {}),
                           (f"integrations/ticket-service/submit/{rid}", {"confirmed": True})):
            code, result = self.request(path, body)
            self.assertEqual(code, 409, result)
        self.assertEqual(self.dashboard.state["posts"], [])
        self.request(f"automation/{rid}", {"action": "cancel"})
        self.assertEqual(self.request(f"tickets/{rid}", {})[0], 200)

    def test_cap_exceeded_blocks_whole_job_without_truncation_or_posts(self):
        rid = self.record()
        with patch.object(tickets, "generate_drafts", return_value=self.generated(rid, 6)):
            state = self.run_job(rid)
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["ticket_count"], 6)
        self.assertEqual(len(tickets.load_tickets(rid)["data"]["tickets"]), 6)
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_ai_failure_retries_without_paid_or_evidence_fallback(self):
        rid = self.record(automation={**self.policy, "use_ai": True})
        with patch.object(tickets, "_ollama_decompose", side_effect=tickets.DraftGenerationError("Local AI unavailable")) as model:
            state = self.run_job(rid)
        self.assertEqual(state["status"], "retry_wait")
        self.assertIn("Local AI unavailable", state["error"])
        model.assert_called_once()
        self.assertIsNone(tickets.load_tickets(rid))
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_ai_proposal_preserves_observation_hypothesis_boundary_in_delivery(self):
        rid = self.record(automation={**self.policy, "use_ai": True})
        model = '[{"title":"Selection is lost", "description":"Selection vanished", "evidence":"Database erased", "steps_to_reproduce":["Delete all data"], "event_indices":[2]}]'
        with patch.object(tickets, "_ollama_decompose", return_value=model):
            state = self.run_job(rid)
        self.assertEqual(state["status"], "delivered", state)
        draft = tickets.load_tickets(rid)["data"]["tickets"][0]
        self.assertTrue(draft["title"].startswith("Investigate:"))
        self.assertNotIn("Delete all data", draft["steps_to_reproduce"])
        self.assertNotIn("Database erased", draft["evidence"])
        self.assertIn("do not establish a bug", draft["evidence"])
        payload = self.dashboard.state["posts"][0]
        self.assertIn("Unverified AI proposal", payload["detail"])
        self.assertIn('"proposal_verified": false', payload["detail"])
        self.assertNotIn("Delete all data", payload["detail"])

    def test_lost_ack_retry_reuses_frozen_content_seed_and_receipt(self):
        rid = self.record()
        self.dashboard.state["drop_after_write"] = True
        first = self.run_job(rid)
        self.assertEqual(first["status"], "retry_wait", first)
        original = copy.deepcopy(self.dashboard.state["posts"][0])
        edited = tickets.load_tickets(rid)["data"]
        edited["tickets"][0]["title"] = "An unrelated later edit"
        tickets.save_tickets(rid, edited)
        automation.control(rid, "retry")
        with patch.object(tickets, "generate_drafts", side_effect=AssertionError("Frozen drafts must not regenerate")):
            final = self.run_job(rid)
        self.assertEqual(final["status"], "delivered", final)
        self.assertEqual(self.dashboard.state["posts"], [original, original])
        self.assertEqual(len(self.dashboard.state["items"]), 1)
        self.assertFalse(final["deliveries"][0]["error"])

    def test_retry_budget_and_backoff_survive_manual_retry(self):
        rid = self.record()
        self.dashboard.state["readback_unavailable"] = True
        for attempt in range(1, 5):
            before = time.time()
            state = self.run_job(rid)
            self.assertEqual(state["attempts"], attempt)
            if attempt < 4:
                self.assertEqual(state["status"], "retry_wait")
                self.assertAlmostEqual(state["next_retry_at"] - before, automation.RETRY_DELAYS[attempt - 1], delta=2)
                self.assertFalse(automation.run_once())
                automation.control(rid, "retry")
            else:
                self.assertEqual(state["status"], "blocked")
                self.assertIsNone(state["next_retry_at"])
                self.assertEqual(self.request(f"automation/{rid}", {"action": "retry"})[0], 400)
        self.assertEqual(len(self.dashboard.state["posts"]), 1)
        automation.recover_jobs(resume_inflight=True)
        self.assertFalse(automation.run_once())

    def test_refusal_is_blocked_and_not_automatically_retried(self):
        rid = self.record()
        self.dashboard.state["refuse"] = True
        state = self.run_job(rid)
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["deliveries"][0]["state"], "refused")
        self.assertFalse(automation.run_once())

    def test_destination_change_or_invalid_destination_blocks_before_send(self):
        for target in ("http://127.0.0.1:1", "https://example.test"):
            rid = self.record(sessionId=target)
            with patch.dict(os.environ, {"PANTHER_DASHBOARD_URL": target}):
                state = self.run_job(rid)
            self.assertEqual(state["status"], "blocked")
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_expired_retry_returns_blocked_without_500_or_send(self):
        rid = self.record()
        self.dashboard.state["readback_unavailable"] = True
        self.run_job(rid)
        with patch.object(automation.time, "time", return_value=self.start + 86401):
            code, result = self.request(f"automation/{rid}", {"action": "retry"})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("expired", result["error"])
        self.assertEqual(len(self.dashboard.state["posts"]), 1)

    def test_waiting_for_an_adapter_keeps_cancellation_accessible(self):
        rid = self.record()
        entered, release = threading.Event(), threading.Event()
        worker_errors = []
        original_submit = integrations.submit_ticket

        def hold_send(recording, ticket, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic delivery wait exceeded its bound")
            self.assertEqual(kwargs.get("expected_payload_hash"),
                             automation._load(rid)["frozen"]["items"][0]["payload_hash"])
            return original_submit(recording, ticket, **kwargs)

        def run():
            try:
                automation.run_once()
            except Exception as exc:
                worker_errors.append(exc)

        with patch.object(integrations, "submit_ticket", side_effect=hold_send):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(3))
            cancel_thread = threading.Thread(target=lambda: automation.control(rid, "cancel"))
            cancel_thread.start()
            try:
                cancel_thread.join(0.5)
                self.assertFalse(cancel_thread.is_alive(), "Cancellation must persist while the adapter is waiting")
            finally:
                release.set()
                worker.join(5)
                cancel_thread.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(self.job(rid)["status"], "cancelled")
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_cancel_after_preview_before_network_prevents_all_sends(self):
        rid = self.record()
        generated = self.generated(rid, 2)
        original = integrations.submit_ticket
        entered, release = threading.Event(), threading.Event()

        def hold_send(recording, ticket, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(recording, ticket, **kwargs)

        with patch.object(tickets, "generate_drafts", return_value=generated), \
                patch.object(integrations, "submit_ticket", side_effect=hold_send):
            worker = threading.Thread(target=automation.run_once)
            worker.start()
            self.assertTrue(entered.wait(3))
            cancelled = []
            cancel_thread = threading.Thread(target=lambda: cancelled.append(automation.control(rid, "cancel")))
            cancel_thread.start()
            deadline = time.monotonic() + 3
            while rid not in automation._CANCELLED and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn(rid, automation._CANCELLED)
            release.set()
            worker.join(5)
            cancel_thread.join(5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(cancel_thread.is_alive())
        state = self.job(rid)
        self.assertEqual(state["status"], "cancelled", state)
        self.assertEqual(state["delivered_count"], 0)
        self.assertEqual(len(self.dashboard.state["posts"]), 0)
        self.assertEqual(self.request(f"automation/{rid}", {"action": "retry"})[0], 400)
        self.assertTrue(cancelled)

    def test_delete_during_generation_cannot_recreate_capture_or_job(self):
        rid = self.record()
        generated = self.generated(rid, 1)

        def delete_then_return(*args, **kwargs):
            storage.delete_recording(rid)
            return generated

        with patch.object(tickets, "generate_drafts", side_effect=delete_then_return):
            self.assertTrue(automation.run_once())
        self.assertIsNone(storage.get_recording(rid))
        self.assertFalse(automation._path(rid).exists())
        self.assertFalse((storage.TICKETS_DIR / f"{rid}.json").exists())
        self.assertEqual(self.dashboard.state["posts"], [])

    def github_receipt_job(self):
        target = "https://api.github.com/repos/example/widgets"
        with patch.dict(os.environ, {"PANTHER_GITHUB_REPO": "example/widgets"}):
            rid = self.record(automation={**self.policy, "target_kind": "github", "target_url": target})
            entry = storage.get_recording(rid)
            draft = self.generated(rid, 1)["tickets"][0]
            snapshot = automation._snapshot(entry)
            preview = destinations.preview("github", snapshot, draft)
        job = automation._load(rid)
        job.update(status="delivering", attempts=1, ticket_count=1,
                   frozen={"recording": snapshot, "items": [{"ticket": draft,
                           "payload_hash": preview["payload_hash"], "seed_key": preview["seed_key"]}]})
        automation._save(job)
        receipt = {"target": target, "seed_key": preview["seed_key"], "payload_hash": preview["payload_hash"],
                   "state": "delivered", "verified": True, "item": 7, "created": True, "verified_at": 1700000000}
        storage.write_json(integrations._receipt_path(target, preview["seed_key"]), receipt)
        return rid

    def test_github_status_and_restart_recover_the_destination_issue_identity(self):
        rid = self.github_receipt_job()
        with patch.object(github, "_request", side_effect=AssertionError("Receipt recovery must not contact GitHub")):
            automation.recover_jobs(resume_inflight=True)
            for _ in range(2):
                code, state = self.request(f"automation/{rid}")
                self.assertEqual(code, 200, state)
                self.assertEqual(state["status"], "delivered")
                self.assertEqual(state["target_kind"], "github")
                delivery = state["deliveries"][0]
                self.assertEqual(delivery["ticket_key"], "#7")
                self.assertEqual(delivery["ticket_url"], "https://github.com/example/widgets/issues/7")
                self.assertEqual(delivery["verified_at"], 1700000000)
                self.assertTrue(delivery["previously_verified"])
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_deleted_github_capture_keeps_destination_kind_and_issue_identity(self):
        rid = self.github_receipt_job()
        with patch.object(github, "_request", side_effect=AssertionError("Cancellation must not contact GitHub")):
            code, result = self.request(f"recordings/{rid}", method="DELETE")
            self.assertEqual(code, 200, result)
            self.assertTrue(result["deleted"])
            code, state = self.request(f"automation/{rid}")
        self.assertEqual(code, 200, state)
        self.assertEqual(state["status"], "cancelled")
        self.assertFalse(state["upload_cancelled"])
        self.assertEqual(state.get("target_kind"), "github")
        self.assertEqual(state["target_url"], "https://api.github.com/repos/example/widgets")
        self.assertEqual(state["deliveries"][0]["ticket_key"], "#7")
        self.assertEqual(state["deliveries"][0]["ticket_url"], "https://github.com/example/widgets/issues/7")
        self.assertEqual(state["delivered_count"], 1)
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_restart_recovers_same_frozen_snapshot_and_verified_receipt(self):
        rid = self.record()
        self.run_job(rid)
        job = automation._load(rid)
        job.update(status="delivering", deliveries=[], delivered_count=0)
        automation._save(job)
        automation.recover_jobs(resume_inflight=True)
        self.assertEqual(automation._load(rid)["status"], "queued")
        with patch.object(tickets, "generate_drafts", side_effect=AssertionError("Do not regenerate")):
            state = self.run_job(rid)
        self.assertEqual(state["status"], "delivered")
        self.assertTrue(state["deliveries"][0]["previously_verified"])
        self.assertEqual(len(self.dashboard.state["posts"]), 1)

    def test_missing_initial_queue_write_recovers_only_explicit_admission(self):
        rid = self.record()
        automation._path(rid).unlink()
        manual = self.record(automation=None, sessionId="manual")
        automation.recover_jobs(resume_inflight=True)
        self.assertTrue(automation._path(rid).exists())
        self.assertFalse(automation._path(manual).exists())
        self.assertEqual(self.run_job(rid)["status"], "delivered")

    def test_lost_job_after_processing_started_never_regenerates_or_resets_authority(self):
        rid = self.record()
        self.dashboard.state["drop_after_write"] = True
        self.run_job(rid)
        automation._path(rid).unlink()
        automation.recover_jobs(resume_inflight=True)
        with patch.object(tickets, "generate_drafts", side_effect=AssertionError("Missing delivery snapshot must block")):
            self.assertFalse(automation.run_once())
        state = self.job(rid)
        self.assertEqual(state["status"], "blocked")
        self.assertIn("history is unknown", state["error"])
        self.assertEqual(len(self.dashboard.state["posts"]), 1)

    def test_reading_delivered_status_does_not_rewrite_completion_freshness(self):
        rid = self.record()
        self.run_job(rid)
        original = automation._path(rid).read_bytes()
        with patch.object(automation.time, "time", return_value=time.time() + 100):
            state = self.job(rid)
        self.assertEqual(state["status"], "delivered")
        self.assertEqual(automation._path(rid).read_bytes(), original)

    def test_corrupt_job_is_preserved_and_cannot_deliver(self):
        rid = self.record()
        storage.write_json(automation._path(rid), {"broken": True})
        with self.assertLogs("automation", level="ERROR"):
            automation.recover_jobs(resume_inflight=True)
        self.assertFalse(automation.run_once())
        self.assertEqual(json.loads(automation._path(rid).read_text()), {"broken": True})
        self.assertEqual(self.request(f"automation/{rid}")[0], 400)
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_receipt_for_different_content_is_never_counted_verified(self):
        rid = self.record()
        self.dashboard.state["readback_unavailable"] = True
        self.run_job(rid)
        job = automation._load(rid)
        item = job["frozen"]["items"][0]
        receipt = integrations._load_receipt(self.target, item["seed_key"])
        receipt.update(payload_hash="f" * 64, state="delivered", verified=True, verified_at=time.time())
        storage.write_json(integrations._receipt_path(self.target, item["seed_key"]), receipt)
        state = self.job(rid)
        self.assertEqual(state["delivered_count"], 0)
        self.assertEqual(state["deliveries"][0]["state"], "conflict")
        automation.control(rid, "retry")
        self.assertEqual(self.run_job(rid)["status"], "blocked")
        self.assertEqual(len(self.dashboard.state["posts"]), 1)

    def test_changed_receipt_cannot_leave_terminal_success_claim(self):
        rid = self.record()
        self.run_job(rid)
        item = automation._load(rid)["frozen"]["items"][0]
        receipt = integrations._load_receipt(self.target, item["seed_key"])
        receipt["payload_hash"] = "f" * 64
        storage.write_json(integrations._receipt_path(self.target, item["seed_key"]), receipt)
        state = self.job(rid)
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["delivered_count"], 0)
        self.assertEqual(state["deliveries"][0]["state"], "conflict")

    def test_cancel_before_upload_is_durable_ack_and_blocks_late_upload(self):
        rid = storage.recording_id_for_session(self.capture["extensionId"], self.capture["sessionId"])
        code, result = self.request(f"automation/{rid}", {"action": "cancel"})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["upload_cancelled"])
        self.assertEqual(result["delivered_count"], 0)
        automation._CANCELLED.clear()  # Simulate process restart: durable marker still gates admission.
        self.assertEqual(self.request(f"automation/{rid}")[1]["status"], "cancelled")
        code, result = self.request("record", self.capture)
        self.assertEqual(code, 400, result)
        self.assertIn("cancelled", result["error"])
        self.assertIsNone(storage.get_recording(rid))
        self.assertFalse(automation.run_once())
        self.assertEqual(self.dashboard.state["posts"], [])

    def test_delete_removes_private_job_but_retains_receipt_and_replay_revocation(self):
        rid = self.record()
        self.run_job(rid)
        code, _ = self.request(f"recordings/{rid}", method="DELETE")
        self.assertEqual(code, 200)
        self.assertFalse(automation._path(rid).exists())
        self.assertEqual(len(list((self.root / "deliveries").glob("*.json"))), 1)
        self.assertTrue(automation._cancellation_path(rid).is_file())
        automation._CANCELLED.clear()
        self.assertEqual(self.request("record", self.capture)[0], 400)
        removed = self.request(f"automation/{rid}")[1]
        self.assertEqual(removed["delivered_count"], 1)
        self.assertTrue(removed["deliveries"][0]["verified"])
        self.assertFalse(removed["upload_cancelled"])
        self.assertEqual(removed["target_url"], self.target)
        self.assertEqual(removed["expires_at"], self.capture["startTime"] / 1000 + 86400)

    def test_expired_cancellation_marker_is_removed_without_renewal(self):
        rid = storage.recording_id_for_session("test-extension", "automation-test")
        automation.control(rid, "cancel")
        path = automation._cancellation_path(rid)
        marker = json.loads(path.read_text())
        automation.control(rid, "cancel")
        self.assertEqual(json.loads(path.read_text())["expires_at"], marker["expires_at"])
        with patch.object(automation.time, "time", return_value=marker["expires_at"] + 1):
            self.assertFalse(automation.run_once())
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
