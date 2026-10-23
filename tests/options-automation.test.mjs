import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
const source = await readFile(new URL("../extension/options/automation-state.js", import.meta.url), "utf8");
const {automationView, safeTicketURL} = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));

test("active automatic jobs lock draft mutations; retry is bounded to retry_wait", () => {
  for (const status of ["queued", "generating", "delivering", "retry_wait"]) {
    const view = automationView({status, delivered_count: 1, ticket_count: 2});
    assert.equal(view.locked, true); assert.equal(view.poll, true); assert.equal(view.cancel, true);
    assert.equal(view.retry, status === "retry_wait"); assert.match(view.text, /1 of 2/);
  }
});
test("terminal jobs never readmit themselves and manual captures remain editable", () => {
  for (const status of ["delivered", "blocked", "cancelled", "disabled"]) {
    const view = automationView({status});
    assert.equal(view.locked, false); assert.equal(view.retry, false); assert.equal(view.cancel, false); assert.equal(view.poll, false);
    assert.equal(view.hidden, status === "disabled");
  }
});
test("stale and unknown status fail closed without claiming delivery or allowing control", () => {
  for (const job of [null, {status:"future-state"}, {status:"delivered"}, {status:"disabled"}]) {
    const view = automationView(job, true);
    assert.equal(view.locked, true); assert.equal(view.poll, true); assert.equal(view.hidden, false);
    assert.equal(view.retry, false); assert.equal(view.cancel, false); assert.match(view.text, /Status unavailable/);
  }
  assert.equal(automationView(null).locked, true);
});
test("receipt links must match the admitted loopback target", () => {
  const target = "http://127.0.0.1:8080";
  assert.equal(safeTicketURL(target + "/#ticket/example", target), target + "/#ticket/example");
  for (const value of ["javascript:alert(1)", "https://example.com", "http://user@127.0.0.1:8080", "http://127.0.0.1:9999", "http://localhost:8080", "/relative"]) {
    assert.equal(safeTicketURL(value, target), null);
  }
});
test("GitHub receipt links stay within the admitted repository and issue route", () => {
  const target = "https://api.github.com/repos/example/project";
  const href = "https://github.com/example/project/issues/12";
  assert.equal(safeTicketURL(href, target), href);
  for (const value of [
    "https://github.com/example/other/issues/12", "https://github.com/other/project/issues/12",
    "https://github.com/example/project/pull/12", "https://github.com/example/project/issues/0",
    "https://github.com/example/project/issues/12/extra", "https://github.com/example/project/issues/12?token=value",
    "https://github.com/example/project/issues/12#fragment", "https://github.com/example/project/issues/new",
    "http://github.com/example/project/issues/12", "https://user@github.com/example/project/issues/12",
    "https://github.com.attacker.test/example/project/issues/12", "javascript:alert(1)",
  ]) assert.equal(safeTicketURL(value, target), null, value);
  for (const admitted of [
    "https://api.github.com/repos/example/project?token=value", "https://api.github.com/repos/example/project#fragment",
    "https://api.github.com/repos/example/project/extra", "https://api.github.com/repos/example/project/issues",
    "https://api.github.com.attacker.test/repos/example/project", "http://api.github.com/repos/example/project",
  ]) assert.equal(safeTicketURL(href, admitted), null, admitted);
});
