import { describe, it, expect } from "https://deno.land/std@0.224.0/testing/bdd.ts";
import { sanitizeEventPayload } from "./shared/redaction.js";

describe("redaction", () => {
  it("redacts secret field names", () => {
    const out = sanitizeEventPayload({ password: "hunter2", token: "abc", name: "alice" });
    expect(out.password).toBe("***REDACTED***");
    expect(out.token).toBe("***REDACTED***");
    expect(out.name).toBe("alice");
  });

  it("redacts nested secret fields", () => {
    const out = sanitizeEventPayload({ user: { api_key: "sk-123", email: "a@b.c" } });
    expect(out.user.api_key).toBe("***REDACTED***");
    expect(out.user.email).toBe("a@b.c");
  });

  it("does not redact normal interaction data", () => {
    const out = sanitizeEventPayload({ type: "click", tag: "button", text: "Save" });
    expect(out.type).toBe("click");
    expect(out.text).toBe("Save");
  });
});
