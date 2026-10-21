"""Synthetic backend regressions; all data stays in an isolated temporary directory."""
import concurrent.futures
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import inference
import redaction
import storage
import tickets


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="panther-test-")
        self.root = Path(self.temporary.name)
        self.patch = patch.multiple(storage, ROOT=self.root,
                                    RECORDINGS_DIR=self.root / "recordings",
                                    EVENTS_DIR=self.root / "events",
                                    INFERENCE_DIR=self.root / "inference",
                                    CONSENT_LOG=self.root / "consent.log",
                                    MANIFEST_FILE=self.root / "manifest.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def create(self, session="", **kwargs):
        return storage.create_recording(tab_id=1, start_time=1000, end_time=1002,
                                        event_count=1, session_id=session, extension_id="test-extension", **kwargs)

    def test_session_retries_are_idempotent_and_do_not_replace_metadata(self):
        first = self.create("session-1", goal="original")
        second = self.create("session-1", goal="retry")
        self.assertEqual(first, second)
        self.assertEqual(storage.get_recording(first)["goal"], "original")
        self.assertEqual(len(storage.list_recordings()), 1)

    def test_threaded_creates_do_not_lose_records(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda index: self.create(f"session-{index}"), range(32)))
        self.assertEqual(len(set(ids)), 32)
        self.assertEqual({record["id"] for record in storage.list_recordings()}, set(ids))
        self.assertEqual(os.stat(storage.MANIFEST_FILE).st_mode & 0o777, 0o600)

    def test_delete_removes_media_events_screenshots_and_derived_results(self):
        storage.ensure_dirs()
        paths = [storage.RECORDINGS_DIR / filename for filename in ("video.webm", "events.json", "screenshots.json")]
        for path in paths:
            path.write_bytes(b"synthetic fixture")
        rid = self.create(video_path=paths[0], events_path=paths[1], screenshots_path=paths[2])
        storage.save_inference_result(rid, {"narrative": "synthetic"})
        tickets.save_tickets(rid, tickets.generate_drafts(storage.get_recording(rid), []))
        self.assertTrue(storage.delete_recording(rid))
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertFalse(list((self.root / "inference").glob("*.json")))
        self.assertFalse(list((self.root / "tickets").glob("*.json")))
        self.assertIsNone(storage.get_recording(rid))

    def test_ttl_removes_derived_data_when_library_is_read(self):
        rid = self.create()
        tickets.save_tickets(rid, tickets.generate_drafts(storage.get_recording(rid), []))
        manifest = storage._load_manifest()
        manifest[rid]["created_at"] = time.time() - storage.TTL_SECONDS - 10
        storage.write_json(storage.MANIFEST_FILE, manifest)
        self.assertEqual(storage.list_recordings(), [])
        self.assertFalse((self.root / "tickets" / f"{rid}.json").exists())

    def test_corrupt_manifest_is_not_overwritten(self):
        storage.MANIFEST_FILE.write_text("invalid JSON")
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(storage.MANIFEST_FILE.read_text(), "invalid JSON")

    def test_invalid_paths_and_ids_are_rejected(self):
        for invalid in ("../secret", "a/b", "a\\b", "", ".", "x" * 81):
            with self.assertRaises(ValueError):
                storage.get_recording(invalid)
        with self.assertRaises(ValueError):
            self.create(events_path=self.root.parent / "outside.json")

    def test_drafts_persist_as_one_result_and_can_be_retried_after_failure(self):
        rid = self.create()
        recording = storage.get_recording(rid)
        with patch.object(tickets, "_ollama_decompose", side_effect=ConnectionError("offline")):
            with self.assertRaises(tickets.DraftGenerationError):
                tickets.generate_drafts(recording, [], use_ai=True)
        self.assertIsNone(tickets.load_tickets(rid))
        result = tickets.generate_drafts(recording, [])
        result_id = tickets.save_tickets(rid, result)
        self.assertEqual(result_id, tickets.save_tickets(rid, result))
        self.assertEqual(tickets.load_tickets(rid)["data"], result)
        self.assertEqual(len(list((self.root / "tickets").glob("*.json"))), 1)

    def test_switching_modes_preserves_reviewed_edits(self):
        rid = self.create()
        recording = storage.get_recording(rid)
        evidence = tickets.generate_drafts(recording, [])
        evidence["tickets"][0]["title"] = "Reviewed title that must survive"
        tickets.save_tickets(rid, evidence)
        with patch.object(tickets, "_ollama_decompose", return_value='[{"title":"AI suggestion"}]'):
            ai = tickets.generate_drafts(recording, [], use_ai=True)
        tickets.save_tickets(rid, ai)
        self.assertEqual(tickets.load_tickets(rid)["data"]["mode"], "ai")
        previous = tickets.load_tickets(rid, mode="observed")
        self.assertEqual(previous["data"]["tickets"][0]["title"], "Reviewed title that must survive")
        tickets.save_tickets(rid, previous["data"])
        self.assertEqual(tickets.load_tickets(rid)["data"], evidence)
        self.assertEqual(tickets.load_tickets(rid, mode="ai")["data"], ai)

    def test_old_ai_generation_is_not_reused_but_file_is_preserved(self):
        rid = self.create()
        legacy = {"mode": "ai", "tickets": [{"id": "old-draft", "title": "Unsupported claim"}]}
        tickets.save_tickets(rid, legacy)
        path = self.root / "tickets" / f"{rid}.json"
        before = path.read_bytes()
        self.assertIsNone(tickets.load_tickets(rid, mode="ai"))
        self.assertEqual(tickets.load_tickets(rid)["data"], legacy)
        self.assertEqual(path.read_bytes(), before)


class DraftTests(unittest.TestCase):
    recording = {"id": "test-recording", "start_time": 1000, "url": "http://localhost:7878/?token=secret#work",
                 "goal": "Fix the Save button", "title": "Dashboard", "video_path": ""}
    events = [{"type": "click", "ts": 1001500, "target": {"tag": "button", "text": "Save"}}]

    def test_observed_mode_never_calls_model_and_preserves_evidence(self):
        with patch.object(tickets, "_ollama_decompose", side_effect=AssertionError("must not run")):
            result = tickets.generate_drafts(self.recording, self.events)
        draft = result["tickets"][0]
        self.assertEqual(result["mode"], "observed")
        self.assertIsNone(result["model"])
        self.assertEqual(draft["steps_to_reproduce"], ["click: Save"])
        self.assertEqual(draft["provenance"]["source_url"], "http://localhost:7878/#work")
        self.assertEqual(draft["evidence_refs"][0]["event_index"], 1)
        self.assertTrue(draft["requires_review"])
        self.assertFalse(draft["execution_authorized"])
        self.assertEqual(draft["id"], tickets.generate_drafts(self.recording, self.events)["tickets"][0]["id"])

    def test_ai_failure_and_malformed_json_are_explicit_errors(self):
        for raw in ("not json", "{}", '[{"title": null}]', '[4]'):
            with self.subTest(raw=raw), patch.object(tickets, "_ollama_decompose", return_value=raw):
                with self.assertRaises(tickets.DraftGenerationError):
                    tickets.generate_drafts(self.recording, self.events, use_ai=True)

    def test_model_cannot_grant_execution_or_invent_evidence_references(self):
        model = [{"title": "Fix Save", "status": "approved", "execution_authorized": True,
                  "priority": "urgent", "steps_to_reproduce": "not an array",
                  "event_indices": [1, 999, True, -1], "screenshots": [12]}]
        with patch.object(tickets, "_ollama_decompose", return_value=json.dumps(model)):
            draft = tickets.generate_drafts(self.recording, self.events, use_ai=True)["tickets"][0]
        self.assertEqual(draft["status"], "draft")
        self.assertFalse(draft["execution_authorized"])
        self.assertEqual(draft["priority"], "medium")
        self.assertEqual(draft["steps_to_reproduce"], ["click: Save"])
        self.assertEqual([ref["event_index"] for ref in draft["evidence_refs"]], [1])
        self.assertEqual(draft["screenshots"], [])

    def test_hallucinated_model_claims_never_become_observed_evidence(self):
        model = [{"title": "Save loses all work", "description": "The Save button deletes records.",
                  "steps_to_reproduce": ["Save deleted all records", "Error dialog proves data loss"],
                  "evidence": "Confirmed data loss shown by error dialog", "event_indices": [1, 999]}]
        with patch.object(tickets, "_ollama_decompose", return_value=json.dumps(model)):
            result = tickets.generate_drafts(self.recording, self.events, use_ai=True)
        draft = result["tickets"][0]
        self.assertEqual(result["generation_revision"], 2)
        self.assertEqual(draft["steps_to_reproduce"], ["click: Save"])
        self.assertIn("do not establish a bug", draft["evidence"])
        self.assertNotIn("data loss", draft["evidence"])
        self.assertNotIn("dialog", draft["evidence"])
        self.assertTrue(draft["title"].startswith("Investigate: "))
        self.assertTrue(draft["description"].startswith("Unverified AI proposal: "))
        self.assertEqual(draft["hypothesis"], model[0]["description"])
        self.assertEqual(draft["suggested_verification_steps"], model[0]["steps_to_reproduce"])
        self.assertFalse(draft["provenance"]["proposal_verified"])

    def test_ai_without_valid_references_uses_observed_context_only(self):
        with patch.object(tickets, "_ollama_decompose", return_value='[{"title":"Investigate saving","event_indices":[500]}]'):
            draft = tickets.generate_drafts(self.recording, self.events, use_ai=True)["tickets"][0]
        self.assertEqual(draft["title"], "Investigate saving")
        self.assertEqual(draft["steps_to_reproduce"], ["click: Save"])
        self.assertEqual([ref["event_index"] for ref in draft["evidence_refs"]], [1])

    def test_model_cannot_reorder_the_observed_timeline(self):
        events = self.events + [{"type": "click", "ts": 1001600, "target": {"tag": "button", "label": "Cancel"}}]
        with patch.object(tickets, "_ollama_decompose", return_value='[{"title":"Investigate saving","event_indices":[2,1,2]}]'):
            draft = tickets.generate_drafts(self.recording, events, use_ai=True)["tickets"][0]
        self.assertEqual(draft["steps_to_reproduce"], ["click: Save", "click: Cancel"])
        self.assertEqual([ref["event_index"] for ref in draft["evidence_refs"]], [1, 2])

    def test_frame_sampler_receives_relative_seconds(self):
        with patch.object(tickets, "_sample_key_frames", return_value=[]) as sample, \
             patch.object(tickets, "_ollama_decompose", return_value="[]"):
            tickets.generate_drafts(self.recording, self.events, use_ai=True)
        sample.assert_called_once_with("", [1.5])

    def test_recording_evidence_scope_is_bounded_and_marked(self):
        draft = tickets.generate_drafts(self.recording, self.events * 400)["tickets"][0]
        self.assertEqual(draft["provenance"]["event_count"], 300)
        self.assertTrue(draft["provenance"]["events_truncated"])
        self.assertEqual(draft["provenance"]["events_total"], 400)


class PrivacyTests(unittest.TestCase):
    def test_url_redaction_and_nested_secret_descriptors(self):
        events = [{"type": "change", "url": "https://user:pass@example.test/path?token=abc#access_token=def",
                   "target": {"name": "password", "value": "very-secret", "text": "very-secret"}}]
        clean = redaction.sanitize_events(events)[0]
        self.assertEqual(clean["url"], "https://example.test/path")
        self.assertNotIn("value", clean["target"])
        self.assertNotIn("very-secret", json.dumps(clean))

    def test_server_event_allowlist_discards_input_values_keys_and_page_text(self):
        raw = [{"type": "change", "ts": 1234, "value": "private input", "key": "a",
                "page": {"text": "private page"}, "target": {"tag": "input", "value": "private input", "text": "private input", "label": "private input"}},
               {"type": "keydown", "key": "password"},
               {"type": "click", "target": {"tag": "button", "label": "Bearer abc123 person@example.test"}}]
        clean = redaction.sanitize_events(raw)
        self.assertEqual(len(clean), 2)
        self.assertEqual(clean[0], {"type": "change", "ts": 1234, "url": "", "target": {"tag": "input"}})
        self.assertEqual(clean[1]["target"]["label"], "[REDACTED] [EMAIL]")
        self.assertNotIn("private", json.dumps(clean))
        self.assertEqual(redaction.sanitize_url("https://example.test/token/topsecret"), "https://example.test/token/REDACTED")

    def test_inference_cannot_use_remote_or_credentialed_endpoints(self):
        for endpoint in ("https://api.example.test/v1", "http://192.168.1.20/v1", "http://user:pass@localhost/v1"):
            with self.subTest(endpoint=endpoint), patch.object(inference, "OLLAMA_ENDPOINT", endpoint):
                with self.assertRaises(ValueError):
                    inference.local_endpoint()
        with patch.object(inference, "OLLAMA_ENDPOINT", "http://127.0.0.1:11434/v1/"):
            self.assertEqual(inference.local_endpoint(), "http://127.0.0.1:11434/v1")


if __name__ == "__main__":
    unittest.main()
