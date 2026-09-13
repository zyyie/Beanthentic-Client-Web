/**
 * Poll pending client transaction status and push bell notifications
 * when the farmer approves, even when the user is not on the transaction page.
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

  function mergeStatusFromApi(body, pending) {
    const next = Object.assign({}, pending || {});
    const rc = (body && body.receipt) || {};
    next.reference_no =
      (body && body.reference_no) || rc.reference_no || rc.ref || next.reference_no;
    if (body && body.farmer_name) next.farmer_name = String(body.farmer_name).trim();
    if (body && body.farmer_id != null && body.farmer_id !== "") {
      next.farmer_id = String(body.farmer_id);
    }
    if (body && body.buyer_name) next.client_name = String(body.buyer_name).trim();
    if (body && body.product) next.product_type = String(body.product).trim();
    if (body && body.pickup_date) next.pickup_date = String(body.pickup_date).trim();
    if (body && body.payment_amount != null) next.payment_amount = body.payment_amount;
    if (body && body.payment_method) next.payment_method = String(body.payment_method).trim();
    if (body && body.order_selections) next.order_selections = body.order_selections;
    if (body && body.receipt_date) next.receipt_date = String(body.receipt_date).trim();
    if (body && body.receipt_time) next.receipt_time = String(body.receipt_time).trim();
    if (body && body.transaction_at) next.transaction_at = String(body.transaction_at).trim();
    else if (rc.transaction_at) next.transaction_at = String(rc.transaction_at).trim();
    else if (rc.at) next.transaction_at = String(rc.at).trim();
    next.receipt = rc;
    return next;
  }

  function notify(type, ref, extra) {
    const N = window.BeanthenticNotifs;
    if (!N || !N.notifyTransactionEvent) return;
    N.notifyTransactionEvent(type, ref, extra);
  }

  function ensureApprovedNotification(pending, body) {
    const N = window.BeanthenticNotifs;
    if (!N || !N.ensureApprovedNotification || !pending || !pending.reference_no) return;
    N.ensureApprovedNotification(String(pending.reference_no).trim(), {
      farmer_name:
        (body && body.farmer_name) ||
        pending.farmer_name ||
        "",
      datetime:
        (body && body.transaction_at) ||
        pending.approved_at ||
        pending.submitted_at ||
        "",
    });
    if (N.refresh) N.refresh();
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

        let next = mergeStatusFromApi(body, pending);
        next.reference_no = ref;
        next.status = String(next.status || "pending");

        const notifyExtra = {
          farmer_name: next.farmer_name || pending.farmer_name || "",
        };

        if (body.is_sent_to_client) {
          if (next.status !== "sent_to_client") {
            next.status = "sent_to_client";
            savePending(next);
          }
          return;
        }

        if (body.is_approved) {
          if (next.status !== "approved" && next.status !== "sent_to_client") {
            next.status = "approved";
            savePending(next);
            notify("approved", ref, notifyExtra);
          } else {
            savePending(next);
            ensureApprovedNotification(next, body);
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
