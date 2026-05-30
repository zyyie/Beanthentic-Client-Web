(function () {
  const NOTIF_LIST_KEY = "beanthentic_notifications";
  const READ_KEY = "beanthentic_notifications_read";

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

  function defaultNotifs() {
    const routes = loadNotifRoutes();
    return [
      {
        id: "welcome",
        title: "Welcome to Beanthentic",
        text: "Explore farmer profiles, history, and transactions from your dashboard.",
        time: "Just now",
        datetime: "2026-05-22",
        href: routes.home || "/",
        kind: "info",
      },
      {
        id: "farmer-new",
        title: "New farmer registered",
        text: "A new farmer profile has been added to the system.",
        time: "1 day ago",
        datetime: "2026-05-21",
        href: routes.farmer_profiles || "/farmer-profiles",
        kind: "farmer",
      },
      {
        id: "system-update",
        title: "System update",
        text: "Coffee history and report features are now available.",
        time: "2 days ago",
        datetime: "2026-05-20",
        href: routes.report || "/report",
        kind: "info",
      },
    ];
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

  function mergeNotifications() {
    const readIds = loadReadIds();
    const stored = loadStoredNotifs();
    const byId = new Map();

    defaultNotifs().forEach((n) => {
      byId.set(n.id, { ...n, unread: !readIds.has(n.id) });
    });

    stored.forEach((n) => {
      if (!n || !n.id) return;
      const prev = byId.get(n.id) || {};
      byId.set(n.id, {
        ...prev,
        ...n,
        unread: !readIds.has(n.id),
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

  function pushNotification(notif) {
    if (!notif || !notif.id) return false;
    const stored = loadStoredNotifs();
    if (stored.some((n) => n.id === notif.id)) return false;
    const entry = {
      id: String(notif.id),
      title: notif.title || "Notification",
      text: notif.text || "",
      time: notif.time || "Just now",
      datetime: notif.datetime || new Date().toISOString().slice(0, 10),
      href: notif.href || "",
      kind: notif.kind || "info",
      reference_no: notif.reference_no || "",
    };
    stored.unshift(entry);
    saveStoredNotifs(stored);
    return true;
  }

  function markRead(id) {
    const readIds = loadReadIds();
    readIds.add(id);
    saveReadIds(readIds);
  }

  function markAllRead() {
    const readIds = loadReadIds();
    mergeNotifications().forEach((n) => readIds.add(n.id));
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

    const card = document.createElement("div");
    card.className = "header-notif-item-card";

    const title = document.createElement("p");
    title.className = "header-notif-item-title";
    title.textContent = notif.title || "Notification";

    const text = document.createElement("p");
    text.className = "header-notif-item-text";
    text.textContent = notif.text || "";

    const meta = document.createElement("div");
    meta.className = "header-notif-item-meta";

    const timeEl = document.createElement("time");
    timeEl.className = "header-notif-item-time";
    timeEl.dateTime = notif.datetime || "";
    timeEl.textContent = notif.time || "";
    meta.appendChild(timeEl);

    if (href) {
      const viewLink = document.createElement("a");
      viewLink.className = "header-notif-item-go";
      viewLink.href = href;
      viewLink.setAttribute("data-notif-view", "");
      viewLink.innerHTML =
        'View<span class="header-notif-item-go-arrow" aria-hidden="true"> →</span>';
      meta.appendChild(viewLink);
    }

    card.appendChild(title);
    card.appendChild(text);
    card.appendChild(meta);
    li.appendChild(card);
    return li;
  }

  function renderList() {
    if (!uiRoot) return;
    const list = uiRoot.querySelector("[data-notif-list]");
    const badge = uiRoot.querySelector("[data-notif-badge]");
    const empty = uiRoot.querySelector("[data-notif-empty]");
    if (!list) return;

    list.innerHTML = "";
    const notifs = mergeNotifications();
    notifs.forEach((notif) => list.appendChild(renderListItem(notif)));

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
    list.hidden = !hasItems;
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
    pushTransactionSubmitted(referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      const txBase = loadNotifRoutes().transaction || "/transaction";
      const added = pushNotification({
        id: "tx-submitted-" + ref,
        kind: "transaction",
        title: "Transaction submitted",
        text:
          (extra && extra.text) ||
          `Your request was sent (Ref: ${ref}). We will notify you when the farmer approves it.`,
        time: "Just now",
        datetime: new Date().toISOString(),
        href: txBase + "?ref=" + encodeURIComponent(ref),
        reference_no: ref,
      });
      renderList();
      return added;
    },
    pushTransactionApproved(referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      const added = pushNotification({
        id: "tx-approved-" + ref,
        kind: "transaction",
        title: "Transaction approved",
        text:
          (extra && extra.text) ||
          `The farmer approved your transaction (Ref: ${ref}). Waiting for the official receipt.`,
        time: "Just now",
        datetime: new Date().toISOString(),
        href:
          (loadNotifRoutes().transaction || "/transaction") +
          "?ref=" +
          encodeURIComponent(ref),
        reference_no: ref,
      });
      renderList();
      return added;
    },
    pushTransactionReceiptSent(referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      const added = pushNotification({
        id: "tx-receipt-" + ref,
        kind: "transaction",
        title: "Receipt sent",
        text:
          (extra && extra.text) ||
          `The farmer sent your official receipt (Ref: ${ref}). Tap to view your receipt.`,
        time: "Just now",
        datetime: new Date().toISOString(),
        href:
          (loadNotifRoutes().transaction || "/transaction") +
          "?ref=" +
          encodeURIComponent(ref),
        reference_no: ref,
      });
      renderList();
      return added;
    },
    notifyTransactionEvent(type, referenceNo, extra) {
      const ref = String(referenceNo || "").trim();
      if (!ref) return false;
      let added = false;
      if (type === "submitted") {
        added = this.pushTransactionSubmitted(ref, extra);
      } else if (type === "approved") {
        added = this.pushTransactionApproved(ref, extra);
      } else if (type === "receipt") {
        added = this.pushTransactionReceiptSent(ref, extra);
      }
      if (!added) return false;
      const titles = {
        submitted: "Transaction submitted",
        approved: "Transaction approved",
        receipt: "Receipt sent",
      };
      const texts = {
        submitted: "Ref " + ref + " — waiting for farmer approval.",
        approved: "Ref " + ref + " — farmer approved your request.",
        receipt: "Ref " + ref + " — tap to view your receipt.",
      };
      if (showToast) {
        showToast({
          title: (extra && extra.title) || titles[type] || "Transaction update",
          text: (extra && extra.text) || texts[type] || "",
          type: "success",
          durationMs: 7000,
        });
      }
      pulseBell();
      return true;
    },
    markAllRead() {
      markAllRead();
      renderList();
    },
    list: mergeNotifications,
    refresh: renderList,
  };

  let uiBound = false;

  function bindNotificationUi() {
    if (uiBound) {
      renderList();
      return true;
    }
    uiRoot = document.querySelector("[data-notif-root]");
    if (!uiRoot) return false;

    const toggle = uiRoot.querySelector("#notif-toggle");
    const panel = uiRoot.querySelector("#notif-panel");
    const markReadBtn = uiRoot.querySelector("[data-notif-mark-read]");
    const list = uiRoot.querySelector("[data-notif-list]");

    if (!toggle || !panel || !list) return false;

    function setPanelOpen(open) {
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      panel.classList.toggle("is-open", open);
      if (open) renderList();
    }

    function navigateFromNotification(item, href) {
      const target = normalizeHref(href);
      if (!item || !target) return;
      const id = item.getAttribute("data-notif-id");
      if (id) markRead(id);
      setPanelOpen(false);
      renderList();
      window.location.assign(target);
    }

    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      setPanelOpen(panel.hasAttribute("hidden"));
    });

    markReadBtn?.addEventListener("click", (e) => {
      e.stopPropagation();
      window.BeanthenticNotifs.markAllRead();
    });

    list.addEventListener("click", (e) => {
      const viewLink = e.target.closest("[data-notif-view]");
      if (!viewLink) return;
      e.preventDefault();
      e.stopPropagation();
      const item = viewLink.closest(".header-notif-item");
      const href =
        viewLink.getAttribute("href") ||
        item?.getAttribute("data-notif-href") ||
        "";
      navigateFromNotification(item, href);
    });

    document.addEventListener("click", (e) => {
      if (!uiRoot.contains(e.target)) setPanelOpen(false);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setPanelOpen(false);
    });

    setPanelOpen(false);
    renderList();
    uiBound = true;
    return true;
  }

  if (!bindNotificationUi()) {
    document.addEventListener("DOMContentLoaded", function () {
      bindNotificationUi();
    });
  }
})();
