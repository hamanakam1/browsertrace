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

    def test_retention_does_not_delete_artifacts_from_corrupt_metadata(self):
        storage.ensure_dirs()
        events = storage.EVENTS_DIR / "retained.json"
        events.write_bytes(b"retained capture")
        rid = self.create(events_path=events)
        original = storage._load_manifest()
        for timestamp in (None, "yesterday", -1, True, float("nan"), float("inf")):
            with self.subTest(timestamp=timestamp):
                damaged = json.loads(json.dumps(original))
                if timestamp is None:
                    damaged[rid].pop("created_at")
                else:
                    damaged[rid]["created_at"] = timestamp
                # Deliberately model an externally corrupted file, including
                # nonfinite values that the normal writer refuses to emit.
                storage.MANIFEST_FILE.write_text(json.dumps(damaged))
                before = storage.MANIFEST_FILE.read_bytes()
                with self.assertRaises(ValueError):
                    storage.list_recordings()
                self.assertEqual(events.read_bytes(), b"retained capture")
                self.assertEqual(storage.MANIFEST_FILE.read_bytes(), before)

    def test_corrupt_identity_and_path_fail_before_deleting_any_artifact(self):
        storage.ensure_dirs()
        events = storage.EVENTS_DIR / "retained.json"
        events.write_bytes(b"retained capture")
        rid = self.create(events_path=events)
        original = storage._load_manifest()
        for key, value in (("id", "different-record"), ("screenshots_path", str(self.root.parent / "outside.json")), ("inference", [])):
            with self.subTest(field=key):
                damaged = json.loads(json.dumps(original))
                damaged[rid][key] = value
                storage.write_json(storage.MANIFEST_FILE, damaged)
                before = storage.MANIFEST_FILE.read_bytes()
                with self.assertRaises(ValueError):
                    storage.delete_recording(rid)
                self.assertEqual(events.read_bytes(), b"retained capture")
                self.assertEqual(storage.MANIFEST_FILE.read_bytes(), before)

    def test_failed_deletion_commit_preserves_existing_capture(self):
        storage.ensure_dirs()
        events = storage.EVENTS_DIR / "retained.json"
        events.write_bytes(b"retained capture")
        rid = self.create(events_path=events)
        before = storage.MANIFEST_FILE.read_bytes()
        with patch.object(storage, "_save_manifest", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                storage.delete_recording(rid)
        self.assertEqual(events.read_bytes(), b"retained capture")
        self.assertEqual(storage.MANIFEST_FILE.read_bytes(), before)

    def test_interrupted_deletion_is_durable_and_resumes_on_next_read(self):
        storage.ensure_dirs()
        events = storage.EVENTS_DIR / "removing.json"
        events.write_bytes(b"synthetic capture")
        rid = self.create(events_path=events)
        save = storage._save_manifest
        calls = 0

        def fail_final_save(manifest):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("final save interrupted")
            save(manifest)

        with patch.object(storage, "_save_manifest", side_effect=fail_final_save):
            with self.assertRaises(OSError):
                storage.delete_recording(rid)
        self.assertTrue(storage._load_manifest()[rid]["_deleting"])
        self.assertFalse(events.exists())
        self.assertEqual(storage.list_recordings(), [])
        self.assertEqual(storage._load_manifest(), {})

    def test_cap_eviction_commit_failure_preserves_old_capture(self):
        storage.ensure_dirs()
        events = storage.EVENTS_DIR / "old.json"
        events.write_bytes(b"retained until committed")
        old_id = self.create(events_path=events)
        with patch.object(storage, "MAX_RECORDINGS", 1), \
             patch.object(storage, "_save_manifest", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(events.read_bytes(), b"retained until committed")
        self.assertEqual([row["id"] for row in storage.list_recordings()], [old_id])

    def test_cap_eviction_removes_only_oldest_capture_and_derived_data(self):
        storage.ensure_dirs()
        events = storage.EVENTS_DIR / "oldest.json"
        events.write_bytes(b"oldest synthetic capture")
        oldest = self.create(events_path=events)
        tickets.save_tickets(oldest, tickets.generate_drafts(storage.get_recording(oldest), []))
        second = self.create()
        with patch.object(storage, "MAX_RECORDINGS", 2):
            newest = self.create()
            self.assertEqual({row["id"] for row in storage.list_recordings()}, {second, newest})
        self.assertFalse(events.exists())
        self.assertFalse((self.root / "tickets" / f"{oldest}.json").exists())

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
    recording = {"id": "test-recording", "start_time": 1000, "url": "http://localhost:8080/?token=secret#work",
                 "goal": "Fix the Save button", "title": "Dashboard", "video_path": ""}
    # The extension sends the accessible name as "label"; it never sends page text.
    events = [{"type": "click", "ts": 1001500, "target": {"tag": "button", "label": "Save"}}]

    def test_observed_mode_never_calls_model_and_preserves_evidence(self):
        with patch.object(tickets, "_ollama_decompose", side_effect=AssertionError("must not run")):
            result = tickets.generate_drafts(self.recording, self.events)
        draft = result["tickets"][0]
        self.assertEqual(result["mode"], "observed")
        self.assertIsNone(result["model"])
        self.assertEqual(draft["steps_to_reproduce"], ["click: Save"])
        self.assertEqual(draft["provenance"]["source_url"], "http://localhost:8080/#work")
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

    def test_truncated_evidence_says_so_in_words(self):
        """The reader of a draft or a delivered ticket sees the evidence sentence."""
        draft = tickets.generate_drafts(self.recording, self.events * 400)["tickets"][0]
        self.assertIn("The first 300 of 400 recorded events were used", draft["evidence"])
        self.assertIn("the rest were not analysed", draft["evidence"])
        self.assertNotIn("400 recorded events;", draft["evidence"])

    def test_ai_evidence_says_so_in_words_when_truncated(self):
        with patch.object(tickets, "_ollama_decompose", return_value='[{"title": "Investigate saving", "event_indices": [1]}]'):
            draft = tickets.generate_drafts(self.recording, self.events * 400, use_ai=True)["tickets"][0]
        self.assertIn("The first 300 of 400 recorded events were used", draft["evidence"])
        self.assertIn("the rest were not analysed", draft["evidence"])

    def test_untruncated_evidence_keeps_its_plain_wording(self):
        draft = tickets.generate_drafts(self.recording, self.events)["tickets"][0]
        self.assertEqual(draft["evidence"], "1 recorded event; no AI inference was performed.")
        self.assertFalse(draft["provenance"]["events_truncated"])


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

    def test_session_identifiers_never_survive_a_url(self):
        """A session identifier is a credential in disguise; the extension redacts it."""
        self.assertEqual(redaction.sanitize_url("https://example.test/session/abc123"),
                         "https://example.test/session/REDACTED")
        self.assertEqual(redaction.sanitize_url("https://example.test/my-session-id/42"),
                         "https://example.test/my-session-id/REDACTED")
        self.assertEqual(redaction.sanitize_url("https://example.test/#session"),
                         "https://example.test/")

    def test_an_event_always_carries_a_timestamp(self):
        """Without one, the event drops out of the ticket's reproduction steps."""
        for payload in ({"type": "click", "url": "https://example.test/"},
                        {"type": "click", "ts": "later", "url": "https://example.test/"}):
            with self.subTest(payload=payload):
                clean = redaction.sanitize_capture_event(payload)
                self.assertIsInstance(clean["ts"], int)
                self.assertGreater(clean["ts"], 0)

    def test_page_text_is_not_read_from_a_text_field(self):
        """The extension sends an accessible name as "label" and never page text."""
        clean = redaction.sanitize_capture_event(
            {"type": "click", "ts": 1, "target": {"tag": "button", "text": "private page text"}}
        )
        self.assertEqual(clean["target"], {"tag": "button"})
        self.assertNotIn("private", json.dumps(clean))


if __name__ == "__main__":
    unittest.main()
