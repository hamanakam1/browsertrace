"""The GitHub destination: an explicit, narrow, verified remote delivery."""
import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
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
            if "issue_pages" in state:
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("page", ["1"])[0])
                return self.reply(200, state["issue_pages"].get(page, []))
            return self.reply(200, state.get("listed_issues", list(state["issues"].values())))
        if self.path.startswith(f"/repos/{REPO}/issues/"):
            number = int(self.path.rsplit("/", 1)[-1])
            issue = state["issues"].get(number)
            if issue is None:
                return self.reply(404, {"message": "Not Found"})
            if state.get("readback_unavailable"):
                return self.reply(503, {"message": "unavailable"})
            return self.reply(200, issue)
        self.reply(404, {"message": "Not Found"})

    def do_POST(self):
        state = self.server.state
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["posts"].append(payload)
        state["headers"].append(dict(self.headers))
        if state.get("create_fails"):
            return self.reply(422, {"message": "Validation Failed"})
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
        self.env = patch.dict(os.environ, {"PANTHER_GITHUB_TOKEN": TOKEN, "PANTHER_GITHUB_REPO": REPO})
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
        self.server.state["issues"][42] = {"number": 42, "title": "Keep form changes", "body": body, "state": "open"}
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ticket_key"], "#42")
        self.assertEqual(self.server.state["posts"], [])

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

    def test_retry_reads_the_receipted_issue_even_when_it_is_not_in_recent_issues(self):
        self.server.state["readback_unavailable"] = True
        first = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(first["ok"])
        self.server.state["readback_unavailable"] = False
        self.server.state["listed_issues"] = []
        self.server.state["gets"].clear()
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ticket_key"], "#7")
        self.assertEqual(len(self.server.state["posts"]), 1)
        self.assertEqual(self.server.state["gets"], [f"/repos/{REPO}/issues/7"])

    def test_concurrent_submissions_share_one_creation(self):
        first_lookup = threading.Event()
        second_lookup = threading.Event()
        release_first = threading.Event()
        original = github._find_existing

        def delayed_lookup(*args, **kwargs):
            result = original(*args, **kwargs)
            if first_lookup.is_set():
                second_lookup.set()
            else:
                first_lookup.set()
                if not release_first.wait(5):
                    raise AssertionError("First lookup was not released")
            return result

        with patch.object(github, "_find_existing", side_effect=delayed_lookup):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(github.submit_ticket, self.recording, self.ticket)
                try:
                    self.assertTrue(first_lookup.wait(5))
                    second = pool.submit(github.submit_ticket, self.recording, self.ticket)
                    raced = second_lookup.wait(0.2)
                finally:
                    release_first.set()
                results = [first.result(timeout=5), second.result(timeout=5)]
        self.assertFalse(raced, "A second submission entered creation while the first was pending")
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_a_lost_creation_response_is_never_blindly_posted_again(self):
        original = github._request

        def lost_response(path, payload=None, **kwargs):
            if payload is not None:
                kwargs["before_send"]()
                raise TimeoutError("Synthetic lost response")
            return original(path, payload, **kwargs)

        with patch.object(github, "_request", side_effect=lost_response):
            first = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(first["ok"])
        retry = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(retry["ok"], retry)
        self.assertEqual(retry["state"], "pending")
        self.assertEqual(self.server.state["posts"], [])

    def test_a_lost_response_can_recover_an_exact_issue_from_the_marker(self):
        original = github._request

        def lost_response(path, payload=None, **kwargs):
            response = original(path, payload, **kwargs)
            if payload is not None:
                raise TimeoutError("Synthetic lost response")
            return response

        with patch.object(github, "_request", side_effect=lost_response):
            first = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(first["ok"])
        retry = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(len(self.server.state["posts"]), 1)

    def test_a_full_recent_issue_page_does_not_prove_the_identity_is_absent(self):
        self.server.state["listed_issues"] = [
            {"number": number, "body": "Unrelated issue", "state": "open"}
            for number in range(1, 101)
        ]
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.server.state["posts"], [])
        self.assertEqual(len(self.server.state["gets"]), github.MAX_ISSUES_SCANNED // github.ISSUES_PER_PAGE)

    def test_marker_lookup_recovers_an_issue_on_a_later_page(self):
        body = github.preview_ticket(self.recording, self.ticket)["payload"]["body"]
        existing = {"number": 42, "title": self.ticket["title"], "body": body, "state": "open"}
        self.server.state["issues"][42] = existing
        self.server.state["issue_pages"] = {
            1: [{"number": number, "body": "Unrelated", "state": "open"} for number in range(100, 200)],
            2: [existing],
        }
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["ticket_key"], "#42")
        self.assertEqual(self.server.state["posts"], [])
        self.assertEqual(len(self.server.state["gets"]), 3)

    def test_creation_can_follow_a_complete_multi_page_scan(self):
        self.server.state["issue_pages"] = {
            1: [{"number": number, "body": None, "pull_request": {}} for number in range(100, 200)],
            2: [{"number": 99, "body": "Unrelated", "state": "closed"}],
        }
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.server.state["posts"]), 1)
        self.assertTrue(any("page=2" in path for path in self.server.state["gets"]))

    def test_malformed_issue_entries_never_establish_absence(self):
        for entry in (None, {"number": True}, {"number": 1, "body": []}):
            with self.subTest(entry=entry), patch.object(github, "_request", return_value=[entry]):
                with self.assertRaisesRegex(ValueError, "unexpected issue"):
                    github._find_existing("synthetic-identity", REPO)

    def test_marker_scan_stops_after_the_time_budget(self):
        full_page = [{"number": number, "body": "Unrelated"} for number in range(1, 101)]
        with patch.object(github, "_request", return_value=full_page) as request, \
                patch.object(github.time, "monotonic", side_effect=[100, 101, 116]):
            with self.assertRaisesRegex(ValueError, "time budget expired"):
                github._find_existing("synthetic-identity", REPO)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["timeout"], 14)

    def test_a_malformed_later_page_prevents_creation(self):
        self.server.state["issue_pages"] = {
            1: [{"number": number, "body": "Unrelated"} for number in range(100, 200)],
            2: {"message": "Unexpected shape"},
        }
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.server.state["posts"], [])
        self.assertEqual(len(self.server.state["gets"]), 2)

    def test_readback_must_match_the_complete_review_fenced_body(self):
        preview = github.preview_ticket(self.recording, self.ticket)
        body = preview["payload"]["body"].replace(github.FENCE, "Fence removed")
        self.server.state["issues"][42] = {
            "number": 42, "title": self.ticket["title"], "body": body, "state": "open",
        }
        result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["verified"])
        self.assertNotIn("No issue was created", result["error"])
        self.assertEqual(self.server.state["posts"], [])

    def test_result_links_use_the_receipted_destination(self):
        first = github.submit_ticket(self.recording, self.ticket)
        with patch.dict(os.environ, {"PANTHER_GITHUB_REPO": "example/another"}):
            result = github._result(first["receipt"])
        self.assertEqual(result["ticket_url"], f"https://github.com/{REPO}/issues/7")

    def test_configuration_change_during_lookup_prevents_creation(self):
        original = github._find_existing

        def changed_destination(*args, **kwargs):
            result = original(*args, **kwargs)
            os.environ["PANTHER_GITHUB_REPO"] = "example/another"
            return result

        with patch.object(github, "_find_existing", side_effect=changed_destination):
            result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"], result)
        self.assertIn("destination changed", result["error"])
        self.assertEqual(self.server.state["posts"], [])

    def test_configuration_change_after_manual_confirmation_prevents_creation(self):
        preview = github.preview_ticket(self.recording, self.ticket)
        with patch.dict(os.environ, {"PANTHER_GITHUB_REPO": "example/another"}):
            result = destinations.submit("github", self.recording, self.ticket,
                                         expected_payload_hash=preview["payload_hash"])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(self.server.state["gets"], [])
        self.assertEqual(self.server.state["posts"], [])

    def test_remote_error_details_cannot_echo_a_credential_into_receipts(self):
        self.server.state["create_fails"] = True
        original = FakeGitHub.reply

        def reflected_error(handler, code, payload):
            if code == 422:
                payload = {"message": f"Credential {TOKEN} was rejected"}
            return original(handler, code, payload)

        with patch.object(FakeGitHub, "reply", reflected_error):
            result = github.submit_ticket(self.recording, self.ticket)
        self.assertFalse(result["ok"])
        self.assertNotIn(TOKEN, json.dumps(result))
        for receipt in (storage.ROOT / "deliveries").glob("*.json"):
            self.assertNotIn(TOKEN, receipt.read_text())

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
