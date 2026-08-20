/**
 * Shared localStorage helpers for client pending transactions.
 */
(function () {
  "use strict";

  const STORAGE_KEY_LEGACY = "beanthentic_client_pending_tx";

  function pendingStorageKey(farmerId) {
    const fid = String(farmerId || "").trim();
    return fid ? STORAGE_KEY_LEGACY + "_f" + fid : STORAGE_KEY_LEGACY;
  }

  function parsePending(raw) {
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch {
      return null;
    }
  }

  function readRawForKey(key) {
    try {
      return sessionStorage.getItem(key) || localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function readLatestPendingTx() {
    let best = null;
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (!key || key.indexOf(STORAGE_KEY_LEGACY) !== 0) continue;
        const state = parsePending(readRawForKey(key));
        if (!state || !state.reference_no) continue;
        if (
          !best ||
          String(state.submitted_at || "") > String(best.submitted_at || "")
        ) {
          best = state;
        }
      }
      const legacy = parsePending(readRawForKey(STORAGE_KEY_LEGACY));
      if (
        legacy &&
        legacy.reference_no &&
        (!best ||
          String(legacy.submitted_at || "") > String(best.submitted_at || ""))
      ) {
        best = legacy;
      }
    } catch {
      /* ignore */
    }
    return best;
  }

  function readAllPendingTx() {
    const byRef = new Map();
    function add(state) {
      if (!state || !state.reference_no) return;
      const ref = String(state.reference_no).trim();
      if (!ref) return;
      const prev = byRef.get(ref) || {};
      byRef.set(ref, Object.assign({}, prev, state, { reference_no: ref }));
    }
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (!key || key.indexOf(STORAGE_KEY_LEGACY) !== 0) continue;
        add(parsePending(readRawForKey(key)));
      }
      add(parsePending(readRawForKey(STORAGE_KEY_LEGACY)));
    } catch {
      /* ignore */
    }
    return [...byRef.values()];
  }

  function clientNameFromPending(state) {
    if (!state) return "";
    return String(
      state.client_name || state.buyer_name || state.buyer || ""
    ).trim();
  }

  window.BeanthenticTxStorage = {
    STORAGE_KEY_LEGACY,
    pendingStorageKey,
    readLatestPendingTx,
    readAllPendingTx,
    clientNameFromPending,
  };
})();
