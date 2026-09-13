/**
 * Poll for new farmer registrations and push client notifications.
 */
(function () {
  "use strict";

  const KNOWN_FARMERS_KEY = "beanthentic_known_farmer_ids";
  const SEEDED_KEY = "beanthentic_farmer_notif_seeded";
  const NOTIF_LIST_KEY = "beanthentic_notifications";
  const POLL_MS = 5000;
  const ERROR_POLL_MS = 8000;

  let pollTimer = null;
  let polling = false;

  function loadKnownFarmerIds() {
    try {
      const raw = localStorage.getItem(KNOWN_FARMERS_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? new Set(arr.map(String)) : new Set();
    } catch {
      return new Set();
    }
  }

  function saveKnownFarmerIds(set) {
    try {
      localStorage.setItem(KNOWN_FARMERS_KEY, JSON.stringify([...set]));
    } catch {
      /* ignore */
    }
  }

  function mergeKnownFromStoredNotifications(known) {
    try {
      const raw = localStorage.getItem(NOTIF_LIST_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(arr)) return known;
      arr.forEach(function (n) {
        if (!n) return;
        if (n.kind === "farmer" && n.farmer_id) {
          known.add(String(n.farmer_id));
        }
        const id = String(n.id || "");
        if (id.indexOf("farmer-reg-") === 0) {
          known.add(id.slice("farmer-reg-".length));
        }
      });
    } catch {
      /* ignore */
    }
    return known;
  }

  function farmerDisplayName(row) {
    if (row && row.display_name) {
      return String(row.display_name).trim();
    }
    const first = String((row && row.first_name) || "").trim();
    const last = String((row && row.last_name) || "").trim();
    const full = (first + " " + last).trim();
    if (full) return full;
    const username = String((row && row.username) || "").trim();
    if (username) return username;
    const fid = String((row && row.farmer_id) || "").trim();
    return fid ? "Farmer #" + fid : "New farmer";
  }

  function alreadyStoredFarmerNotification(fid) {
    try {
      const raw = localStorage.getItem(NOTIF_LIST_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(arr)) return false;
      return arr.some(function (n) {
        return n && String(n.id) === "farmer-reg-" + fid;
      });
    } catch {
      return false;
    }
  }

  function notifyFarmerRegistered(row) {
    const N = window.BeanthenticNotifs;
    if (!N || !N.pushFarmerRegistered) return false;
    const fid = String((row && row.farmer_id) || "").trim();
    if (!fid) return false;
    const name = farmerDisplayName(row);
    return N.pushFarmerRegistered(fid, name, row.created_at || row.updated_at || "");
  }

  function processFarmers(farmers, seeded) {
    const known = mergeKnownFromStoredNotifications(loadKnownFarmerIds());
    const nextKnown = new Set(known);
    let added = 0;

    farmers.forEach(function (row) {
      const fid = String((row && row.farmer_id) || "").trim();
      if (!fid) return;

      if (!seeded) {
        nextKnown.add(fid);
        return;
      }

      if (nextKnown.has(fid) || alreadyStoredFarmerNotification(fid)) {
        nextKnown.add(fid);
        return;
      }

      nextKnown.add(fid);
      if (notifyFarmerRegistered(row)) {
        added += 1;
      }
    });

    saveKnownFarmerIds(nextKnown);
    if (!seeded) {
      try {
        localStorage.setItem(SEEDED_KEY, "1");
      } catch {
        /* ignore */
      }
    }
    return added;
  }

  function scheduleNextPoll(delayMs) {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(pollFarmers, delayMs);
  }

  function pollFarmers() {
    if (polling) return;
    if (!window.BeanthenticNotifs) {
      scheduleNextPoll(POLL_MS);
      return;
    }

    polling = true;
    fetch("/api/notifications/farmers", {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (result) {
        const body = result.body;
        if (!body || body.ok !== true || !Array.isArray(body.farmers)) {
          scheduleNextPoll(ERROR_POLL_MS);
          return;
        }
        let seeded = false;
        try {
          seeded = localStorage.getItem(SEEDED_KEY) === "1";
        } catch {
          seeded = false;
        }
        processFarmers(body.farmers, seeded);
        scheduleNextPoll(POLL_MS);
      })
      .catch(function () {
        scheduleNextPoll(ERROR_POLL_MS);
      })
      .finally(function () {
        polling = false;
      });
  }

  function start() {
    const known = mergeKnownFromStoredNotifications(loadKnownFarmerIds());
    saveKnownFarmerIds(known);
    pollFarmers();
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) pollFarmers();
    });
    window.addEventListener("focus", pollFarmers);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
