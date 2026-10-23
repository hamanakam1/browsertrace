// Pure view policy: stale or unknown automation must never unlock draft mutations.
export const ACTIVE_AUTOMATION = new Set(["queued", "generating", "delivering", "retry_wait"]);
const TERMINAL_AUTOMATION = new Set(["disabled", "delivered", "blocked", "cancelled"]);
export function automationView(job, stale = false) {
  const status = job?.status || "unknown";
  const active = ACTIVE_AUTOMATION.has(status);
  const delivered = Number.isInteger(job?.delivered_count) ? job.delivered_count : 0;
  const count = Number.isInteger(job?.ticket_count) ? job.ticket_count : 0;
  const labels = {
    queued: "Queued for automatic draft generation.",
    generating: "Generating unreviewed proposals…",
    delivering: "Delivering and independently verifying tickets…",
    retry_wait: "Waiting to retry delivery automatically.",
    delivered: "Automatic delivery finished.",
    blocked: "Automatic delivery is blocked. Start a new consented capture after resolving the cause.",
    cancelled: "Remaining automatic delivery cancelled. Already submitted tickets remain at their destination.",
    disabled: "Manual capture — automatic delivery was not enabled."
  };
  return {
    hidden: status === "disabled" && !stale,
    locked: stale || !TERMINAL_AUTOMATION.has(status),
    poll: stale || active || !TERMINAL_AUTOMATION.has(status),
    retry: !stale && status === "retry_wait",
    cancel: !stale && active,
    text: (stale ? "Status unavailable; last known state: " + status + ". " : (labels[status] || "Checking automatic delivery status…") + " ") +
      delivered + " of " + count + " ticket(s) verified at their recorded delivery time."
  };
}

export function safeTicketURL(value, target) {
  try {
    const url = new URL(value);
    const base = new URL(target);
    if (url.username || url.password || base.username || base.password) return null;
    if (["http:", "https:"].includes(url.protocol) && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
      return url.origin === base.origin ? url.href : null;
    }
    // GitHub's admitted target is the API repository, while its issue link is
    // on the website. Allow only that exact repository and a numeric issue.
    const repo = base.pathname.match(/^\/repos\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)$/)?.[1];
    if (base.origin !== "https://api.github.com" || base.search || base.hash || !repo) return null;
    if (url.origin !== "https://github.com" || url.search || url.hash) return null;
    const prefix = "/" + repo + "/issues/";
    return url.pathname.startsWith(prefix) && /^[1-9][0-9]*$/.test(url.pathname.slice(prefix.length)) ? url.href : null;
  } catch (_) { return null; }
}
