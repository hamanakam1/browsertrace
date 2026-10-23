"""The GitHub destination: an explicit, narrow, verified remote delivery."""
import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import destinations  # noqa: E402
import github  # noqa: E402
import storage  # noqa: E402

TOKEN = "ghp_" + "a" * 36
REPO = "example/widgets"


class FakeGitHub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        state = self.server.state
        state["gets"].append(self.path)
        state["headers"].append(dict(self.headers))
        if self.path == f"/repos/{REPO}":
            if state.get("repo_unavailable"):
                return self.reply(404, {"message": "Not Found"})
            return self.reply(200, {"full_name": REPO, "private": False})
        if self.path.startswith(f"/repos/{REPO}/issues?"):
            return self.reply(200, list(state["issues"].values()))
        if self.path.startswith(f"/repos/{REPO}/issues/"):
            number = int(self.path.rsplit("/", 1)[-1])
            issue = state["issues"].get(number)
            if issue is None:
                return self.reply(404, {"message": "Not Found"})
            if state.get("readback_unavailable"):
                return self.reply(503, {"message": state.get("error_message", "unavailable")})
            return self.reply(200, issue)
        self.reply(404, {"message": "Not Found"})

    def do_POST(self):
        state = self.server.state
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["posts"].append(payload)
        state["headers"].append(dict(self.headers))
        if state.get("create_fails"):
            return self.reply(422, {"message": state.get("error_message", "Validation Failed")})
        number = state["next_number"]
        state["next_number"] += 1
        issue = {"number": number, "title": payload["title"], "body": payload["body"],
                 "state": "open", "labels": [{"name": name} for name in payload.get("labels", [])]}
        state["issues"][number] = issue
        self.reply(201, issue)


class GitHubDestinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        self.server.state = {"gets": [], "posts": [], "headers": [], "issues": {}, "next_number": 7}
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.root = patch.object(storage, "ROOT", Path(self.temp.name))
        self.root.start()
        self.env = patch.dict(os.environ, {"PANTHER_GITHUB_TOKEN": TOKEN, "PANTHER_GITHUB_REPO": REPO,
                                                "PANTHER_DASHBOARD_URL": self.url})
        self.env.start()
        self.host = patch.object(github, "API_HOST", self.url)
        self.host.start()
        self.recording = {"id": "capture-123", "start_time": 100, "end_time": 130,
                          "source_url": "https://example.test/form", "video_path": ""}
        self.ticket = {"id": "draft-1", "title": "Keep form changes", "description": "Preserve the draft.",
                       "steps_to_reproduce": ["Edit the form"],
                       "acceptance_criteria": ["The edit is retained"],
                       "evidence": "Draft text disappeared.", "screenshots": [],
                       "evidence_refs": [{"type": "event", "event_index": 2}],
                       "provenance": {"model": "local-test", "origin": "model_proposal"}}

    def tearDown(self):
        self.host.stop()
        self.env.stop()
        self.root.stop()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def test_the_destination_is_unconfigured_without_a_repository_and_credential(self):
        with patch.dict(os.environ, {}, clear=True):
            report = github.status()
        self.assertEqual(report["state"], "unconfigured")
        self.assertFalse(report["configured"] if "configured" in report else report["ok"])
        self.assertEqual(self.server.state["posts"], [])

    def test_the_credential_is_never_reported_back(self):
        report = github.configuration()
        self.assertTrue(report["configured"])
        self.assertEqual(report["credential"], "present")
        self.assertNotIn(TOKEN, json.dumps(report))
        self.assertNotIn(TOKEN, json.dumps(github.status()))

    def test_a_credential_readable_by_others_is_not_used(self):
        path = storage.ROOT / "github"
        path.mkdir(parents=True, exist_ok=True)
        token_file = path / "token"
        token_file.write_text(TOKEN)
        token_file.chmod(0o644)
        with patch.dict(os.environ, {"PANTHER_GITHUB_REPO": REPO}, clear=True):
            self.assertFalse(github.configuration()["configured"])

    def test_the_preview_names_the_exact_issue_and_never_contacts_github(self):
        preview = github.preview_ticket(self.recording, self.ticket)
        self.assertEqual(preview["target"], "github")
        self.assertEqual(preview["payload"]["title"], "Keep form changes")
        self.assertIn("not authorized for autonomous execution", preview["payload"]["body"])
        self.assertIn("no-auto", preview["payload"]["labels"])
        self.assertIn("panther-capture-capture-123-draft-1", preview["payload"]["body"])
        self.assertNotIn(TOKEN, json.dumps(preview))
        self.assertEqual(self.server.state["posts"], [])

    def test_a_confirmed_submission_creates_verifies_and_records_a_receipt(self):
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["verified"])
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(result["ticket_key"], "#7")
        self.assertIn("/issues/7", result["ticket_url"])
        self.assertEqual(len(self.server.state["posts"]), 1)
        self.assertNotIn(TOKEN, json.dumps(result))
        receipts = list((storage.ROOT / "deliveries").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt["state"], "delivered")
        self.assertNotIn(TOKEN, json.dumps(receipt))
        self.assertEqual(receipt["target"], self.url + f"/repos/{REPO}")

    def test_the_same_identity_is_never_created_twice(self):
        first = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(first["ok"], first)
        second = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["previously_verified"])
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_an_existing_issue_with_the_identity_is_reused(self):
        body = github.preview_ticket(self.recording, self.ticket)["payload"]["body"]
        self.server.state["issues"][42] = {"number": 42, "title": "Keep form changes", "body": body, "state": "open",
                                             "labels": [{"name": name} for name in ("panther", "needs-triage", "no-auto")]}
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ticket_key"], "#42")
        self.assertEqual(self.server.state["posts"], [])

    def test_readback_requires_the_exact_confirmed_body_and_review_labels(self):
        preview = github.preview_ticket(self.recording, self.ticket)
        for changes in (
            {"body": "Replaced review fence\nIdentity: " + github._search_marker(self.recording["id"], self.ticket["id"])},
            {"labels": []},
        ):
            with self.subTest(changes=changes):
                issue = {"number": 42, "title": preview["payload"]["title"],
                         "body": preview["payload"]["body"], "state": "open",
                         "labels": [{"name": name} for name in preview["payload"]["labels"]], **changes}
                self.server.state["issues"][42] = issue
                result = github.submit_ticket(self.recording, self.ticket)
                self.assertFalse(result["ok"], result)
                self.assertFalse(result["verified"])
                self.assertEqual(self.server.state["posts"], [])

    def test_concurrent_submissions_create_only_one_issue(self):
        first_post = threading.Event()
        release_post = threading.Event()
        second_started = threading.Event()
        second_search = threading.Event()
        original_request = github._request
        original_search = github._find_existing
        searches = []

        def request(path, payload=None, **kwargs):
            if payload is not None:
                first_post.set()
                self.assertTrue(release_post.wait(timeout=5))
            return original_request(path, payload, **kwargs)

        def search(*args, **kwargs):
            searches.append(True)
            if len(searches) > 1:
                second_search.set()
            return original_search(*args, **kwargs)

        def second_submit():
            second_started.set()
            return github.submit_ticket(self.recording, self.ticket)

        with patch.object(github, "_request", side_effect=request), patch.object(github, "_find_existing", side_effect=search):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(github.submit_ticket, self.recording, self.ticket)
                self.assertTrue(first_post.wait(timeout=5))
                available = storage.DATA_LOCK.acquire(timeout=0.2)
                if available:
                    storage.DATA_LOCK.release()
                self.assertTrue(available, "Delivery must not block cancellation through DATA_LOCK")
                second = pool.submit(second_submit)
                self.assertTrue(second_started.wait(timeout=5))
                second_search.wait(timeout=0.2)
                release_post.set()
                results = [first.result(timeout=5), second.result(timeout=5)]
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(len(self.server.state["posts"]), 1)
        self.assertEqual({result["ticket_key"] for result in results}, {"#7"})

    def test_known_pending_issue_is_read_back_directly_without_another_creation(self):
        self.server.state["readback_unavailable"] = True
        first = github.submit_ticket(self.recording, self.ticket)
        self.assertEqual(first["receipt"]["item"], 7)
        self.server.state["readback_unavailable"] = False
        with patch.object(github, "_find_existing", return_value=None) as search:
            result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ticket_key"], "#7")
        search.assert_not_called()
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_lost_creation_response_never_reposts_after_an_inconclusive_search(self):
        original_request = github._request

        def lose_response(path, payload=None, **kwargs):
            response = original_request(path, payload, **kwargs)
            if payload is not None:
                raise urllib.error.URLError("synthetic lost response")
            return response

        with patch.object(github, "_request", side_effect=lose_response):
            first = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(first["ok"])
        with patch.object(github, "_find_existing", return_value=None):
            uncertain = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(uncertain["ok"])
        self.assertEqual(uncertain["state"], "pending")
        recovered = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_changed_destination_cannot_replace_the_confirmed_preview(self):
        preview = github.preview_ticket(self.recording, self.ticket)
        os.environ[github.REPO_ENV] = "example/different"
        with patch.object(github, "_request") as request:
            with self.assertRaisesRegex(ValueError, "confirmed GitHub destination or payload changed"):
                github.submit_ticket(self.recording, self.ticket, expected_payload_hash=preview["payload_hash"])
        request.assert_not_called()
        self.assertFalse((storage.ROOT / "deliveries").exists())

    def test_configuration_change_before_send_blocks_the_write(self):
        original_search = github._find_existing

        def change_target(*args, **kwargs):
            existing = original_search(*args, **kwargs)
            os.environ[github.REPO_ENV] = "example/different"
            return existing

        with patch.object(github, "_find_existing", side_effect=change_target):
            result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.server.state["posts"], [])

    def test_readback_and_link_keep_the_original_destination_after_configuration_changes(self):
        original_request = github._request

        def change_after_creation(path, payload=None, **kwargs):
            response = original_request(path, payload, **kwargs)
            if payload is not None:
                os.environ[github.REPO_ENV] = "example/different"
            return response

        with patch.object(github, "_request", side_effect=change_after_creation):
            result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ticket_url"], f"https://github.com/{REPO}/issues/7")
        self.assertIn(f"/repos/{REPO}/issues/7", self.server.state["gets"])
        self.assertFalse(any("example/different" in path for path in self.server.state["gets"]))

    def test_cancellation_before_dispatch_does_not_consume_the_creation_attempt(self):
        def cancelled():
            raise ValueError("Synthetic cancellation before dispatch")

        first = github.submit_ticket(self.recording, self.ticket, before_send=cancelled)
        self.assertFalse(first["ok"])
        self.assertEqual(self.server.state["posts"], [])
        retried = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_pending_legacy_receipt_without_an_item_is_treated_as_uncertain(self):
        preview = github.preview_ticket(self.recording, self.ticket)
        path = github.integrations._receipt_path(preview["target_url"], preview["seed_key"])
        storage.write_json(path, {"target": preview["target_url"], "seed_key": preview["seed_key"],
                                  "payload_hash": preview["payload_hash"], "state": "pending", "verified": False})
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "pending")
        self.assertEqual(self.server.state["posts"], [])

    def test_failed_search_before_any_post_can_retry_safely(self):
        with patch.object(github, "_find_existing", side_effect=urllib.error.URLError("synthetic lookup failure")):
            first = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(first["ok"])
        self.assertEqual(self.server.state["posts"], [])
        retried = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_remote_error_text_is_not_exposed_in_receipts_or_false_noncreation_claims(self):
        self.server.state["readback_unavailable"] = True
        self.server.state["error_message"] = TOKEN + " private reflected content"
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.server.state["posts"]), 1)
        encoded = json.dumps(result)
        self.assertNotIn(TOKEN, encoded)
        self.assertNotIn("private reflected content", encoded)
        self.assertNotIn("No issue was created", encoded)
        receipts = list((storage.ROOT / "deliveries").glob("*.json"))
        self.assertNotIn(TOKEN, receipts[0].read_text())

    def test_the_same_identity_with_different_content_is_a_conflict(self):
        github.submit_ticket(self.recording, self.ticket)
        changed = dict(self.ticket, title="A different title")
        result = github.submit_ticket(self.recording, changed)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_an_unverifiable_readback_stays_pending_and_does_not_claim_success(self):
        self.server.state["readback_unavailable"] = True
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "pending")
        self.assertFalse(result["verified"])

    def test_a_refused_creation_does_not_claim_delivery(self):
        self.server.state["create_fails"] = True
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["state"], "delivered")

    def test_the_authorization_header_is_used_and_the_credential_stays_in_it(self):
        github.submit_ticket(self.recording, self.ticket)
        headers = self.server.state["headers"]
        self.assertTrue(headers)
        self.assertTrue(any(h.get("Authorization") == f"Bearer {TOKEN}" for h in headers))

    def test_the_host_is_fixed_to_github(self):
        self.host.stop()
        try:
            self.assertEqual(github.API_HOST, "https://api.github.com")
        finally:
            self.host.start()

    def test_the_destination_catalogue_lists_every_destination(self):
        catalogue = destinations.catalogue()
        kinds = [entry["kind"] for entry in catalogue]
        self.assertIn("ticket-service", kinds)
        self.assertIn("github", kinds)
        github_entry = next(entry for entry in catalogue if entry["kind"] == "github")
        self.assertTrue(github_entry["available"])
        self.assertNotIn(TOKEN, json.dumps(catalogue))


if __name__ == "__main__":
    unittest.main()
