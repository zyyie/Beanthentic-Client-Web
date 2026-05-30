/**
 * Poll pending client transaction status and push bell notifications
 * (approved, receipt sent) even when the user is not on the transaction page.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "beanthentic_client_pending_tx";
  const POLL_MS = 8000;

  function apiBase() {
    return String(window.__BEANTHENTIC_APP_SERVER_BASE__ || "").replace(/\/+$/, "");
  }

  function loadPending() {
    try {
      const raw =
        localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.reference_no) return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function savePending(state) {
    try {
      const raw = JSON.stringify(state);
      localStorage.setItem(STORAGE_KEY, raw);
      sessionStorage.setItem(STORAGE_KEY, raw);
    } catch {
      /* ignore */
    }
  }

  function notify(type, ref) {
    const N = window.BeanthenticNotifs;
    if (!N || !N.notifyTransactionEvent) return;
    N.notifyTransactionEvent(type, ref);
  }

  function pollOnce() {
    const base = apiBase();
    const pending = loadPending();
    if (!base || !pending || !pending.reference_no) return;

    const ref = String(pending.reference_no).trim();
    const url =
      base +
      "/api/client_transaction_status.php?reference_no=" +
      encodeURIComponent(ref);

    fetch(url, { method: "GET", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json();
      })
      .then(function (body) {
        if (!body || body.ok !== true) return;

        const next = Object.assign({}, pending, {
          reference_no: ref,
          status: pending.status || "pending",
        });

        if (body.is_sent_to_client) {
          if (next.status !== "sent_to_client") {
            next.status = "sent_to_client";
            savePending(next);
            notify("receipt", ref);
          }
          return;
        }

        if (body.is_approved) {
          if (next.status !== "approved" && next.status !== "sent_to_client") {
            next.status = "approved";
            savePending(next);
            notify("approved", ref);
          }
          return;
        }

        if (body.is_dismissed && next.status !== "dismissed") {
          next.status = "dismissed";
          savePending(next);
        }
      })
      .catch(function () {});
  }

  function start() {
    if (!window.BeanthenticNotifs) return;
    pollOnce();
    window.setInterval(pollOnce, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
