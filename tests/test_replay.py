"""Replay-plan contract tests: a proposal, never an execution."""
import ast
import json
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import replay  # noqa: E402
import tickets  # noqa: E402
from redaction import sanitize_events  # noqa: E402

RECORDING = {
    "id": "replay-capture-1", "url": "http://127.0.0.1:8080/work-board?token=secret",
    "title": "Work board", "goal": "Save the selected filter", "capture_mode": "events",
    "start_time": 1000.25, "end_time": 1002.75,
}
EVENTS = [
    {"type": "navigation", "ts": 1000250, "url": "http://127.0.0.1:8080/work-board"},
    {"type": "click", "ts": 1000500, "url": "http://127.0.0.1:8080/work-board",
     "target": {"tag": "button", "label": "Save", "dataTicket": "M-4242"}},
    {"type": "click", "ts": 1000600, "url": "http://127.0.0.1:8080/work-board",
     "target": {"tag": "button", "label": "Save", "id": "save-button"}},
    {"type": "click", "ts": 1000700, "url": "http://127.0.0.1:8080/work-board",
     "target": {"tag": "div", "id": "filter-tile", "inert": True}},
    {"type": "change", "ts": 1000800, "url": "http://127.0.0.1:8080/work-board",
     "target": {"tag": "input", "id": "filter", "type": "text"}},
    {"type": "submit", "ts": 1000900, "url": "http://127.0.0.1:8080/work-board",
     "target": {"tag": "form", "id": "filter-form"}},
    {"type": "diagnostic", "ts": 1000950, "url": "http://127.0.0.1:8080/work-board",
     "diagnostic": {"kind": "http_error", "message": "A request returned HTTP 500",
                    "source": "http://127.0.0.1:8080/api/tickets", "status": 500}},
]


class ReplayPlanTests(unittest.TestCase):
    def plan(self):
        return replay.build_plan(dict(RECORDING), list(EVENTS))

    def count_synthetic_submissions(self, script):
        """Run only the generated flow against an in-memory browser double."""
        submissions = []

        class Locator:
            def __init__(self, selector):
                self.selector = selector

            def click(self):
                if self.selector == "#save":
                    submissions.append("click")

            def evaluate(self, expression):
                if self.selector == "#save-form" and expression == "form => form.requestSubmit()":
                    submissions.append("submit")

        page = SimpleNamespace(goto=lambda url: None, locator=Locator)
        browser = SimpleNamespace(new_page=lambda: page, close=lambda: None)
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda: browser))
        module = SimpleNamespace(sync_playwright=lambda: nullcontext(playwright))
        namespace = {"__name__": "synthetic_replay"}
        with mock.patch.dict(sys.modules, {"playwright.sync_api": module}):
            exec(compile(script, "synthetic_replay.py", "exec"), namespace)
            namespace["replay"]()
        return len(submissions)

    def test_click_followed_by_submit_does_not_submit_twice(self):
        click = {"type": "click", "target": {"tag": "button", "id": "save", "type": "submit"}}
        submit = {"type": "submit", "target": {"tag": "form", "id": "save-form"}}
        diagnostic = {"type": "diagnostic", "diagnostic": {"kind": "console_error", "message": "Synthetic"}}
        for between in ([], [diagnostic]):
            with self.subTest(diagnostics=bool(between)):
                plan = replay.build_plan(dict(RECORDING), sanitize_events([click, *between, submit]))
                self.assertEqual(self.count_synthetic_submissions(replay.render_playwright(plan)), 1)
                self.assertEqual([step["action"] for step in plan["steps"]], ["navigate", "click", "submit"])
                self.assertFalse(plan["steps"][-1]["replayable"])
                self.assertIn("may already", plan["steps"][-1]["note"])
                self.assertEqual(plan["steps"][-1]["selector"]["value"], "save-form")

    def test_standalone_submit_remains_executable(self):
        submit = {"type": "submit", "target": {"tag": "form", "id": "save-form"}}
        for preceding in (
            [],
            [{"type": "click", "target": {"tag": "button", "id": "open"}},
             {"type": "change", "target": {"tag": "input", "id": "field"}}],
            [{"type": "click", "target": {"tag": "button", "id": "open"}},
             {"type": "navigation", "url": "https://example.test/form"}],
        ):
            with self.subTest(preceding=preceding):
                plan = replay.build_plan(dict(RECORDING), sanitize_events([*preceding, submit]))
                self.assertTrue(plan["steps"][-1]["replayable"])
                self.assertEqual(self.count_synthetic_submissions(replay.render_playwright(plan)), 1)

    def test_deferred_submit_has_a_fixed_review_comment_without_note_injection(self):
        plan = replay.build_plan(dict(RECORDING), [
            {"type": "click", "target": {"tag": "button", "id": "save"}},
            {"type": "submit", "target": {"tag": "form", "id": "save-form"}},
        ])
        plan["steps"][-1]["note"] = "Untrusted note\nprint(123)\n#"
        script = replay.render_playwright(plan)
        review_comment = "# Review this recorded submission before adding an explicit submit action."
        self.assertIn(review_comment, script)
        self.assertLess(script.index('.click()'), script.index(review_comment))
        self.assertLess(script.index(review_comment), script.index('browser.close()'))
        self.assertNotIn("Untrusted note", script)
        self.assertNotIn("requestSubmit", script)
        tree = ast.parse(script)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id == "print" for node in ast.walk(tree)))

    def test_native_button_names_use_the_implicit_role(self):
        for role_fields in ({}, {"role": ""}):
            with self.subTest(role_fields=role_fields):
                events = sanitize_events([{"type": "click", "target": {
                    "tag": "button", "label": "Save", **role_fields,
                }}])
                plan = replay.build_plan(dict(RECORDING), events)
                selector = plan["steps"][-1]["selector"]
                self.assertEqual(selector["strategy"], "role-name")
                self.assertEqual(selector["role"], "button")
                self.assertEqual(selector["value"], "Save")

    def test_native_role_inference_preserves_explicit_role_and_length_guard(self):
        for raw_role, expected_role in (("tab", "tab"), ("button" + " " * 34, None), ("[REDACTED]", None)):
            with self.subTest(raw_role=raw_role):
                plan = replay.build_plan(dict(RECORDING), [{"type": "click", "target": {
                    "tag": "button", "role": raw_role, "label": "Save",
                }}])
                self.assertEqual(plan["steps"][-1]["selector"]["role"], expected_role)

    def test_name_and_text_locators_match_exact_literal_labels(self):
        for target, locator_method in (
            ({"tag": "button", "role": "button", "label": "Save"}, "get_by_role"),
            ({"tag": "a", "label": "Save"}, "get_by_text"),
            ({"tag": "button", "role": "button", "label": "Save\"'); print(123); #\n雪"}, "get_by_role"),
        ):
            with self.subTest(target=target):
                plan = replay.build_plan(dict(RECORDING), [{"type": "click", "target": target}])
                tree = ast.parse(replay.render_playwright(plan))
                locators = [node for node in ast.walk(tree)
                            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == locator_method]
                self.assertEqual(len(locators), 1)
                keywords = {keyword.arg: ast.literal_eval(keyword.value) for keyword in locators[0].keywords}
                self.assertIs(keywords.get("exact"), True)
                label = keywords["name"] if locator_method == "get_by_role" else ast.literal_eval(locators[0].args[0])
                self.assertEqual(label, target["label"])
                self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                     and node.func.id == "print" for node in ast.walk(tree)))

    def test_the_plan_describes_the_recorded_path(self):
        plan = self.plan()
        self.assertEqual(plan["schema_version"], "1.0")
        self.assertEqual(plan["recording"]["id"], "replay-capture-1")
        actions = [step["action"] for step in plan["steps"]]
        self.assertEqual(actions, ["navigate", "click", "click", "click", "change", "submit"])
        self.assertEqual([step["index"] for step in plan["steps"]], [1, 2, 3, 4, 5, 6])

    def test_the_most_stable_handle_wins(self):
        selectors = {step["index"]: step["selector"] for step in self.plan()["steps"]}
        self.assertEqual(selectors[2]["strategy"], "test-id")
        self.assertEqual(selectors[2]["value"], "M-4242")
        self.assertEqual(selectors[2]["css"], '[data-ticket="M-4242"]')
        self.assertEqual(selectors[3]["strategy"], "id")
        self.assertEqual(selectors[3]["css"], "#save-button")
        self.assertTrue(selectors[2]["replayable"])

    def test_a_step_without_a_stable_handle_says_it_is_not_replayable(self):
        plan = replay.build_plan(dict(RECORDING), [
            {"type": "click", "ts": 1000500, "url": "http://127.0.0.1:8080/x", "target": {"tag": "div"}}])
        step = plan["steps"][-1]
        self.assertEqual(step["action"], "click")
        self.assertEqual(step["selector"]["strategy"], "none")
        self.assertFalse(step["replayable"])

    def test_a_changed_field_records_that_it_changed_and_not_its_value(self):
        step = [item for item in self.plan()["steps"] if item["action"] == "change"][0]
        self.assertFalse(step["replayable"])
        self.assertIn("never captured", step["note"])
        self.assertNotIn("value", step)

    def test_the_plan_carries_the_observations_and_the_friction(self):
        plan = self.plan()
        self.assertEqual(len(plan["observations"]), 1)
        self.assertEqual(plan["observations"][0]["status"], 500)
        self.assertTrue(plan["friction"])
        for finding in plan["friction"]:
            self.assertTrue(finding["hypothesis"])

    def test_the_plan_never_authorizes_execution(self):
        plan = self.plan()
        self.assertEqual(plan["authority"], {"export_only": True, "requires_review": True, "execution_authorized": False})
        self.assertFalse(plan["limits"]["executed_by_producer"])
        self.assertFalse(plan["limits"]["values_captured"])
        self.assertTrue(plan["limits"]["requires_review"])

    def test_the_plan_never_leaks_a_secret(self):
        serialized = json.dumps(self.plan())
        self.assertNotIn("secret", serialized)
        self.assertNotIn("token=", serialized)

    def test_a_rendered_script_is_a_proposal_and_carries_no_value(self):
        script = replay.render_playwright(self.plan())
        self.assertIn("Panther never runs it", script)
        self.assertIn('page.goto("http://127.0.0.1:8080/work-board")', script)
        self.assertIn("[data-ticket=", script)
        self.assertIn("M-4242", script)
        self.assertIn('page.locator("#save-button").click()', script)
        # A click on a non-control is still replayed: it is what the person did.
        self.assertIn('page.locator("#filter-tile").click()', script)
        # A field change is left out rather than guessed at.
        self.assertNotIn('page.locator("#filter")', script)
        # A form is submitted, not clicked.
        self.assertIn('page.locator("#filter-form").evaluate("form => form.requestSubmit()")', script)
        self.assertNotIn("secret", script)

    def test_a_step_without_a_handle_is_left_out_of_the_script(self):
        plan = replay.build_plan(dict(RECORDING), [
            {"type": "click", "ts": 1000500, "url": "http://127.0.0.1:8080/x", "target": {"tag": "div"}}])
        script = replay.render_playwright(plan)
        self.assertNotIn(".click()", script)

    def test_href_selectors_remain_literal_data_in_the_generated_script(self):
        for href in (
            "https://example.test/it's",
            "https://example.test/x'+str(print(123))+'",
            'https://example.test/a"b\\c',
            "https://example.test/\u96ea/\u2028/\x01",
        ):
            with self.subTest(href=href):
                events = sanitize_events([{
                    "type": "click", "target": {"tag": "a", "href": href},
                }])
                plan = replay.build_plan({"id": "synthetic-capture"}, events)
                script = replay.render_playwright(plan)
                tree = ast.parse(script)
                locators = [node for node in ast.walk(tree)
                            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "locator"]
                self.assertEqual(len(locators), 1)
                self.assertIsInstance(locators[0].args[0], ast.Constant)
                value = plan["steps"][0]["selector"]["value"]
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                self.assertEqual(locators[0].args[0].value, f'a[href="{escaped}"]')
                self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                     and node.func.id == "print" for node in ast.walk(tree)))

    def test_capture_identity_cannot_add_code_below_the_header_comment(self):
        script = replay.render_playwright({
            "recording": {"id": "synthetic\nprint(123)\n#"}, "steps": [],
        })
        tree = ast.parse(script)
        self.assertEqual(len(tree.body), 3)
        self.assertIsInstance(tree.body[0], ast.ImportFrom)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id == "print" for node in ast.walk(tree)))

    def test_the_plan_is_bounded(self):
        events = [{"type": "click", "ts": 1000000 + index, "url": "http://127.0.0.1:8080/x",
                   "target": {"tag": "button", "id": f"b{index}"}} for index in range(500)]
        plan = replay.build_plan(dict(RECORDING), events)
        self.assertLessEqual(len(plan["steps"]), replay.MAX_STEPS)

    def test_diagnostics_do_not_consume_the_action_limit(self):
        diagnostic = {"type": "diagnostic", "ts": 1000000,
                      "diagnostic": {"kind": "http_error", "status": 500}}
        click = {"type": "click", "target": {"tag": "button", "id": "save"}}
        plan = replay.build_plan(dict(RECORDING), [diagnostic] * replay.MAX_STEPS + [click])
        self.assertEqual([step["action"] for step in plan["steps"]], ["navigate", "click"])

    def test_action_truncation_is_disclosed_in_plan_and_script(self):
        events = [{"type": "click", "target": {"tag": "button", "id": "save"}}] * 250
        plan = replay.build_plan(dict(RECORDING), events)
        self.assertEqual(len(plan["steps"]), replay.MAX_STEPS)
        self.assertEqual(plan["limits"]["omitted_steps"], 51)
        self.assertTrue(plan["limits"]["truncated"])
        self.assertIn("51 additional steps were omitted", replay.render_playwright(plan))

    def test_redacted_labels_are_not_executable_handles(self):
        for label in ("[EMAIL]", "[REDACTED]", "Open [EMAIL]"):
            with self.subTest(label=label):
                plan = replay.build_plan(dict(RECORDING), [
                    {"type": "click", "target": {"tag": "button", "role": "button", "label": label}},
                ])
                self.assertFalse(plan["steps"][-1]["replayable"])
                self.assertNotIn(".click()", replay.render_playwright(plan))

    def test_redacted_label_can_fall_back_to_an_intact_href(self):
        plan = replay.build_plan(dict(RECORDING), [
            {"type": "click", "target": {"tag": "a", "label": "[EMAIL]",
                                          "href": "https://example.test/account"}},
        ])
        self.assertEqual(plan["steps"][-1]["selector"]["strategy"], "href")

    def test_potentially_truncated_labels_fall_back_to_an_intact_href(self):
        for label in (("Open " * 23) + "user@example.test", "Open " * 30):
            with self.subTest(label=label):
                events = sanitize_events([{
                    "type": "click", "target": {"tag": "a", "role": "link", "label": label,
                                                  "href": "https://example.test/account"},
                }])
                self.assertEqual(len(events[0]["target"]["label"]), replay.MAX_LABEL)
                plan = replay.build_plan(dict(RECORDING), events)
                self.assertEqual(plan["steps"][-1]["selector"]["strategy"], "href")


if __name__ == "__main__":
    unittest.main()
