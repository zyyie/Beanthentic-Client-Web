(function () {
  const NOTIF_LIST_KEY = "beanthentic_notifications";
  const READ_KEY = "beanthentic_notifications_read";

  const ALLOWED_KINDS = new Set(["farmer", "transaction", "social", "report"]);

  const CATEGORY_ORDER = ["farmer", "transaction", "social", "report"];
  const CATEGORY_LABELS = {
    farmer: "Farmer Registration",
    transaction: "Transactions",
    social: "Social / Facebook",
    report: "Reports",
  };

  function farmerLabel(extra) {
    const name = String(
      (extra && (extra.farmer_name || extra.farmerName)) || ""
    ).trim();
    return name || "The farmer";
  }

  function loadNotifRoutes() {
    try {
      const el = document.getElementById("beanthentic-notif-routes");
      if (!el) return {};
      const data = JSON.parse(el.textContent || "{}");
      return data && typeof data === "object" ? data : {};
    } catch {
      return {};
    }
  }

  function loadReadIds() {
    try {
      const raw = localStorage.getItem(READ_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? new Set(arr) : new Set();
    } catch {
      return new Set();
    }
  }

  function saveReadIds(set) {
    try {
      localStorage.setItem(READ_KEY, JSON.stringify([...set]));
    } catch {
      /* ignore */
    }
  }

  function loadStoredNotifs() {
    try {
      const raw = localStorage.getItem(NOTIF_LIST_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch {
      return [];
    }
  }

  function saveStoredNotifs(arr) {
    try {
      localStorage.setItem(NOTIF_LIST_KEY, JSON.stringify(arr));
    } catch {
      /* ignore */
    }
  }

  function normalizeKind(kind) {
    const k = String(kind || "").trim().toLowerCase();
    return ALLOWED_KINDS.has(k) ? k : "";
  }

  function parseDate(value) {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatRelativeTime(value) {
    const d = parseDate(value);
    if (!d) return "";
    const diffMs = Date.now() - d.getTime();
    const sec = Math.floor(diffMs / 1000);
    if (sec < 45) return "Just now";
    const min = Math.floor(sec / 60);
    if (min < 60) return min === 1 ? "1 minute ago" : min + " minutes ago";
    const hr = Math.floor(min / 60);
    if (hr < 24) return hr === 1 ? "1 hour ago" : hr + " hours ago";
    const day = Math.floor(hr / 24);
    if (day < 7) return day === 1 ? "1 day ago" : day + " days ago";
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function formatDisplayDateTime(value) {
    const d = parseDate(value);
    if (!d) return "";
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function mergeNotifications() {
    const readIds = loadReadIds();
    const stored = loadStoredNotifs();
    const byId = new Map();

    stored.forEach((n) => {
      if (!n || !n.id) return;
      const kind = normalizeKind(n.kind);
      if (!kind) return;
      byId.set(n.id, {
        ...n,
        kind,
        unread: !readIds.has(String(n.id)),
      });
    });

    const list = [...byId.values()];
    list.sort((a, b) => {
      const da = a.datetime || a.created_at || "";
      const db = b.datetime || b.created_at || "";
      return db.localeCompare(da);
    });
    return list;
  }

  function hasApprovedNotification(ref) {
    const id = "tx-approved-" + String(ref || "").trim();
    if (!id || id === "tx-approved-") return false;
    return loadStoredNotifs().some((n) => n && String(n.id) === id);
  }

  function ensureApprovedNotification(referenceNo, extra) {
    const ref = String(referenceNo || "").trim();
    if (!ref || hasApprovedNotification(ref)) return false;
    const farmer = farmerLabel(extra);
    const datetime =
      (extra && extra.datetime) ||
      (extra && extra.approved_at) ||
      new Date().toISOString();
    const txBase = loadNotifRoutes().transaction || "/transaction";
    return pushNotification({
      id: "tx-approved-" + ref,
      kind: "transaction",
      title: "Transaction approved",
      text:
        (extra && extra.text) ||
        "Your transaction has been approved by " +
          farmer +
          ". Your receipt is now available to view and download.",
      time: formatRelativeTime(datetime),
      datetime,
      href:
        normalizeHref(txBase) +
        "?ref=" +
        encodeURIComponent(ref) +
        "&view=receipt",
      reference_no: ref,
    });
  }

  function syncApprovedFromPendingStorage() {
    const storage = window.BeanthenticTxStorage;
    if (!storage || typeof storage.readAllPendingTx !== "function") return 0;
    let added = 0;
    storage.readAllPendingTx().forEach(function (pending) {
      if (!pending || !pending.reference_no) return;
      const status = String(pending.status || "pending").toLowerCase();
      if (status !== "approved" && status !== "sent_to_client") return;
      if (
        ensureApprovedNotification(String(pending.reference_no).trim(), {
          farmer_name: pending.farmer_name || "",
          datetime: pending.approved_at || pending.submitted_at || "",
        })
      ) {
        added += 1;
      }
    });
    return added;
  }

  function pushNotification(notif) {
    if (!notif || !notif.id) return false;
    const kind = normalizeKind(notif.kind);
    if (!kind) return false;

    const stored = loadStoredNotifs();
    const id = String(notif.id);
    const existingIdx = stored.findIndex((n) => n && String(n.id) === id);
    const datetime =
      notif.datetime || new Date().toISOString();
    const entry = {
      id,
      title: notif.title || "Notification",
      text: notif.text || "",
      time: notif.time || formatRelativeTime(datetime),
      datetime,
      href: notif.href || "",
      kind,
      reference_no: notif.reference_no || "",
      farmer_id: notif.farmer_id || "",
    };

    if (existingIdx >= 0) {
      stored[existingIdx] = { ...stored[existingIdx], ...entry };
      saveStoredNotifs(stored);
      return true;
    }

    stored.unshift(entry);
    saveStoredNotifs(stored);
    return true;
  }

  function markNotificationRead(id) {
    if (!id) return;
    const readIds = loadReadIds();
    readIds.add(String(id));
    saveReadIds(readIds);
  }

  function markAllRead() {
    const readIds = loadReadIds();
    mergeNotifications().forEach((n) => readIds.add(String(n.id)));
    saveReadIds(readIds);
  }

  function normalizeHref(href) {
    const h = String(href || "").trim();
    if (!h) return "";
    if (/^https?:\/\//i.test(h)) return h;
    return h.startsWith("/") ? h : "/" + h;
  }

  function resolveHref(notif) {
    const routes = loadNotifRoutes();
    const txBase = routes.transaction || "/transaction";
    if (notif.href) return normalizeHref(notif.href);
    if (notif.kind === "transaction" && notif.reference_no) {
      return (
        normalizeHref(txBase) +
        "?ref=" +
        encodeURIComponent(String(notif.reference_no))
      );
    }
    if (notif.kind === "farmer" && notif.farmer_id) {
      const base = routes.farmer_profiles || "/farmer-profiles";
      return normalizeHref(base) + "?highlight=" + encodeURIComponent(String(notif.farmer_id));
    }
    if (notif.kind === "social") {
      return normalizeHref(routes.news_updates || "/news-updates");
    }
    if (notif.kind === "report") {
      return normalizeHref(routes.report || "/report");
    }
    return "";
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let uiRoot = null;

  function getToastHost() {
    let host = document.getElementById("beanthentic-toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "beanthentic-toast-host";
      host.className = "beanthentic-toast-host";
      host.setAttribute("aria-live", "polite");
      host.setAttribute("aria-atomic", "true");
      document.body.appendChild(host);
    }
    return host;
  }

  function showToast(opts) {
    const o = opts || {};
    const host = getToastHost();
    const toast = document.createElement("div");
    const type = o.type === "error" ? "error" : "success";
    toast.className = "beanthentic-toast beanthentic-toast--" + type;
    toast.setAttribute("role", "status");
    toast.innerHTML =
      `<p class="beanthentic-toast__title">${escapeHtml(o.title || "Notification")}</p>` +
      (o.text
        ? `<p class="beanthentic-toast__text">${escapeHtml(o.text)}</p>`
        : "");
    host.appendChild(toast);
    requestAnimationFrame(function () {
      toast.classList.add("is-visible");
    });
    const ms = Math.max(2500, Number(o.durationMs) || 5000);
    window.setTimeout(function () {
      toast.classList.remove("is-visible");
      window.setTimeout(function () {
        toast.remove();
      }, 320);
    }, ms);
    return toast;
  }

  function pulseBell() {
    if (!uiRoot) uiRoot = document.querySelector("[data-notif-root]");
    const toggle = uiRoot && uiRoot.querySelector("#notif-toggle");
    if (!toggle) return;
    toggle.classList.add("header-notif-btn--pulse");
    window.setTimeout(function () {
      toggle.classList.remove("header-notif-btn--pulse");
    }, 1200);
    renderList();
  }

  function renderListItem(notif) {
    const li = document.createElement("li");
    li.className =
      "header-notif-item" + (notif.unread ? " is-unread" : " is-read");
    li.setAttribute("data-notif-id", notif.id);
    if (notif.kind) li.setAttribute("data-notif-kind", notif.kind);
    const href = resolveHref(notif);
    if (href) li.setAttribute("data-notif-href", href);
    if (notif.reference_no) {
      li.setAttribute("data-notif-ref", String(notif.reference_no));
    }

    const card = document.createElement(href ? "a" : "div");
    card.className = "header-notif-item-card";
    if (href) {
      card.href = href;
      card.setAttribute("data-notif-view", "");
    }

    const body = document.createElement("span");
    body.className = "header-notif-item-body";

    const headRow = document.createElement("span");
    headRow.className = "header-notif-item-head";

    const title = document.createElement("span");
    title.className = "header-notif-item-title";
    title.textContent = notif.title || "Notification";

    const status = document.createElement("span");
    status.className =
      "header-notif-item-status" +
      (notif.unread ? " is-new" : " is-read-label");
    status.textContent = notif.unread ? "New" : "Read";

    headRow.appendChild(title);
    headRow.appendChild(status);

    const text = document.createElement("span");
    text.className = "header-notif-item-text";
    text.textContent = notif.text || "";

    const timeEl = document.createElement("time");
    timeEl.className = "header-notif-item-time";
    timeEl.dateTime = notif.datetime || "";
    const displayTime =
      formatDisplayDateTime(notif.datetime) ||
      notif.time ||
      formatRelativeTime(notif.datetime);
    timeEl.textContent = displayTime;
    timeEl.title = displayTime;

    body.appendChild(headRow);
    body.appendChild(text);
    body.appendChild(timeEl);
    card.appendChild(body);
    li.appendChild(card);
    return li;
  }

  function groupNotifications(notifs) {
    const groups = new Map();
    CATEGORY_ORDER.forEach((kind) => groups.set(kind, []));
    notifs.forEach((notif) => {
      const kind = normalizeKind(notif.kind);
      if (!kind || !groups.has(kind)) return;
      groups.get(kind).push(notif);
    });
    return groups;
  }

  function renderList() {
    if (!uiRoot) uiRoot = document.querySelector("[data-notif-root]");
    const listHost = document.querySelector("[data-notif-list]");
    const badge = uiRoot?.querySelector("[data-notif-badge]");
    const empty = document.querySelector("[data-notif-empty]");
    if (!listHost) return;

    listHost.innerHTML = "";
    const notifs = mergeNotifications();
    const groups = groupNotifications(notifs);

    CATEGORY_ORDER.forEach((kind) => {
      const items = groups.get(kind) || [];
      if (!items.length) return;

      const section = document.createElement("li");
      section.className = "header-notif-group";
      section.setAttribute("data-notif-group", kind);

      const label = document.createElement("h4");
      label.className = "header-notif-group-label";
      label.textContent = CATEGORY_LABELS[kind] || kind;

      const sublist = document.createElement("ul");
      sublist.className = "header-notif-group-list";
      items.forEach((notif) => sublist.appendChild(renderListItem(notif)));

      section.appendChild(label);
      section.appendChild(sublist);
      listHost.appendChild(section);
    });

    const count = notifs.filter((n) => n.unread).length;
    if (badge) {
      if (count > 0) {
        badge.textContent = count > 9 ? "9+" : String(count);
        badge.hidden = false;
      } else {
        badge.hidden = true;
      }
    }

    const hasItems = notifs.length > 0;
    listHost.hidden = !hasItems;
    if (empty) empty.hidden = hasItems;
  }

  window.BeanthenticNotifs = {
    showToast,
    pulseBell,
    push(notif) {
      const added = pushNotification(notif);
      renderList();
      return added;
    },
    pushFarmerRegistered(farmerId, farmerName, createdAt) {
      const fid = String(farmerId || "").trim();
      if (!fid) return false;
      const name = String(farmerName || "").trim() || "Farmer #" + fid;
      const routes = loadNotifRoutes();
      const datetime = createdAt || new Date().toISOString();
      const added = pushNotification({
        id: "farmer-reg-" + fid,
        kind: "farmer",
        title: "New farmer registered",
        text: "New farmer registered: " + name + ".",
        time: formatRelativeTime(datetime),
        datetime,
        href: routes.farmer_profiles || "/farmer-profiles",
        farmer_id: fid,
      });
      if (added) {
        showToast({
          title: "New farmer registered",
          text: name + " joined Beanthentic.",
          type: "success",
        });
        pulseBell();
      }
      renderList();
      return added;
    },
    pushTransactionApproved(referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      const added = ensureApprovedNotification(ref, extra);
      renderList();
      return added;
    },
    ensureApprovedNotification,
    syncApprovedFromPendingStorage,
    pushTransactionReceiptAvailable(referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      const stored = loadStoredNotifs();
      if (stored.some((n) => n && String(n.id) === "tx-approved-" + ref)) {
        return false;
      }
      const farmer = farmerLabel(extra);
      const datetime = new Date().toISOString();
      const txBase = loadNotifRoutes().transaction || "/transaction";
      const added = pushNotification({
        id: "tx-receipt-" + ref,
        kind: "transaction",
        title: "Receipt available",
        text:
          (extra && extra.text) ||
          "Your transaction has been approved by " +
            farmer +
            ". Your receipt is now available to view and download.",
        time: formatRelativeTime(datetime),
        datetime,
        href:
          normalizeHref(txBase) +
          "?ref=" +
          encodeURIComponent(ref) +
          "&view=receipt",
        reference_no: ref,
      });
      renderList();
      return added;
    },
    pushFacebookSynced(extra) {
      const routes = loadNotifRoutes();
      const datetime = new Date().toISOString();
      const syncKey =
        (extra && extra.sync_key) ||
        datetime.slice(0, 13);
      const added = pushNotification({
        id: "fb-sync-" + syncKey,
        kind: "social",
        title: "Facebook Page update",
        text:
          (extra && extra.text) ||
          "New Facebook Page activity has been synced.",
        time: formatRelativeTime(datetime),
        datetime,
        href: routes.news_updates || "/news-updates",
      });
      if (added) {
        pulseBell();
      }
      renderList();
      return added;
    },
    pushReportReady(extra) {
      const routes = loadNotifRoutes();
      const reportId =
        (extra && extra.report_id) ||
        "latest-" + new Date().toISOString().slice(0, 10);
      const datetime = new Date().toISOString();
      const added = pushNotification({
        id: "report-" + reportId,
        kind: "report",
        title: (extra && extra.title) || "Report update",
        text:
          (extra && extra.text) ||
          "Your report has been submitted and is ready for review.",
        time: formatRelativeTime(datetime),
        datetime,
        href: routes.report || "/report",
      });
      if (added) {
        showToast({
          title: (extra && extra.title) || "Report update",
          text:
            (extra && extra.text) ||
            "Your report has been submitted successfully.",
          type: "success",
        });
        pulseBell();
      }
      renderList();
      return added;
    },
    notifyTransactionEvent(type, referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      if (type === "submitted" || type === "receipt") {
        return false;
      }
      let added = false;
      if (type === "approved") {
        added = this.pushTransactionApproved(ref, extra);
      }
      if (!added) return false;
      const farmer = farmerLabel(extra);
      showToast({
        title: (extra && extra.title) || "Transaction approved",
        text:
          (extra && extra.text) ||
          "Your transaction has been approved by " +
            farmer +
            ". Your receipt is now available to view and download.",
        type: "success",
        durationMs: 7000,
      });
      pulseBell();
      return true;
    },
    markAllRead() {
      markAllRead();
      renderList();
    },
    markNotificationRead(id) {
      markNotificationRead(id);
      renderList();
    },
    list: mergeNotifications,
    refresh: renderList,
  };

  let uiBound = false;

  function portalNotifOverlay() {
    const panel = document.getElementById("notif-panel");
    const backdrop = document.getElementById("notif-backdrop");
    if (panel && panel.parentElement !== document.body) {
      document.body.appendChild(panel);
    }
    if (backdrop && backdrop.parentElement !== document.body) {
      document.body.appendChild(backdrop);
    }
  }

  function bindNotificationUi() {
    if (uiBound) {
      renderList();
      return true;
    }
    uiRoot = document.querySelector("[data-notif-root]");
    if (!uiRoot) return false;

    const toggle = uiRoot.querySelector("#notif-toggle");
    const panel = document.getElementById("notif-panel");
    const backdrop = document.getElementById("notif-backdrop");
    const markReadBtn = panel?.querySelector("[data-notif-mark-read]");
    const list = panel?.querySelector("[data-notif-list]");

    if (!toggle || !panel || !list) return false;

    portalNotifOverlay();

    function setPanelOpen(open) {
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      panel.classList.toggle("is-open", open);
      document.body.classList.toggle("notif-panel-open", open);
      if (backdrop) {
        backdrop.hidden = !open;
        backdrop.setAttribute("aria-hidden", open ? "false" : "true");
      }
      if (open) renderList();
    }

    function navigateFromNotification(item, href) {
      const target = normalizeHref(href);
      if (!item || !target) return;
      const id = item.getAttribute("data-notif-id");
      if (id) {
        markNotificationRead(id);
        item.classList.remove("is-unread");
        item.classList.add("is-read");
        renderList();
      }
      setPanelOpen(false);
      window.location.assign(target);
    }

    toggle.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setPanelOpen(panel.hasAttribute("hidden"));
    });

    markReadBtn?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      window.BeanthenticNotifs.markAllRead();
    });

    backdrop?.addEventListener("click", () => {
      setPanelOpen(false);
    });

    list.addEventListener(
      "click",
      (e) => {
        const card = e.target.closest("[data-notif-view]");
        if (!card) return;
        e.preventDefault();
        e.stopPropagation();
        const item = card.closest(".header-notif-item");
        const href =
          card.getAttribute("href") ||
          item?.getAttribute("data-notif-href") ||
          "";
        navigateFromNotification(item, href);
      },
      true
    );

    document.addEventListener("click", (e) => {
      if (panel.hasAttribute("hidden")) return;
      const t = e.target;
      if (toggle === t || toggle.contains(t)) return;
      if (panel.contains(t)) return;
      if (backdrop && (backdrop === t || backdrop.contains(t))) return;
      setPanelOpen(false);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setPanelOpen(false);
    });

    setPanelOpen(false);
    syncApprovedFromPendingStorage();
    renderList();
    uiBound = true;
    return true;
  }

  portalNotifOverlay();
  syncApprovedFromPendingStorage();

  if (!bindNotificationUi()) {
    document.addEventListener("DOMContentLoaded", function () {
      portalNotifOverlay();
      syncApprovedFromPendingStorage();
      bindNotificationUi();
    });
  } else {
    renderList();
  }
})();
