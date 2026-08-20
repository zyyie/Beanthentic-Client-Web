/**
 * Poll pending client transaction status and push bell notifications
 * (approved, receipt sent) even when the user is not on the transaction page.
 */
(function () {
  "use strict";

  const STORAGE_KEY_LEGACY =
    (window.BeanthenticTxStorage && window.BeanthenticTxStorage.STORAGE_KEY_LEGACY) ||
    "beanthentic_client_pending_tx";
  const POLL_MS = 5000;

  function pendingStorageKey(farmerId) {
    if (window.BeanthenticTxStorage && window.BeanthenticTxStorage.pendingStorageKey) {
      return window.BeanthenticTxStorage.pendingStorageKey(farmerId);
    }
    const fid = String(farmerId || "").trim();
    return fid ? STORAGE_KEY_LEGACY + "_f" + fid : STORAGE_KEY_LEGACY;
  }

  function loadAllPending() {
    const list =
      window.BeanthenticTxStorage && window.BeanthenticTxStorage.readAllPendingTx
        ? window.BeanthenticTxStorage.readAllPendingTx()
        : [];
    return list.filter(function (pending) {
      const status = String(pending.status || "pending");
      return status !== "dismissed" && status !== "sent_to_client";
    });
  }

  function savePending(state) {
    try {
      const raw = JSON.stringify(state);
      const key = pendingStorageKey(state && state.farmer_id);
      localStorage.setItem(key, raw);
      sessionStorage.setItem(key, raw);
    } catch {
      /* ignore */
    }
  }

  function notify(type, ref, extra) {
    const N = window.BeanthenticNotifs;
    if (!N || !N.notifyTransactionEvent) return;
    N.notifyTransactionEvent(type, ref, extra);
  }

  function pollPending(pending) {
    if (!pending || !pending.reference_no) return;

    const ref = String(pending.reference_no).trim();
    const url =
      "/api/client-transaction/status?reference_no=" +
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
        if (body.farmer_name) {
          next.farmer_name = String(body.farmer_name).trim();
        }
        if (body.farmer_id != null && body.farmer_id !== "") {
          next.farmer_id = String(body.farmer_id);
        }
        if (body.buyer_name) {
          next.client_name = String(body.buyer_name).trim();
        }
        const notifyExtra = {
          farmer_name: next.farmer_name || pending.farmer_name || "",
        };

        if (body.is_sent_to_client) {
          if (next.status !== "sent_to_client") {
            next.status = "sent_to_client";
            savePending(next);
            notify("receipt", ref, notifyExtra);
          }
          return;
        }

        if (body.is_approved) {
          if (next.status !== "approved" && next.status !== "sent_to_client") {
            next.status = "approved";
            savePending(next);
            notify("approved", ref, notifyExtra);
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

  function pollOnce() {
    const pendings = loadAllPending();
    if (!pendings.length) return;
    pendings.forEach(pollPending);
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
