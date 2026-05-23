(function () {
  const NOTIF_LIST_KEY = "beanthentic_notifications";
  const READ_KEY = "beanthentic_notifications_read";

  const DEFAULT_NOTIFS = [
    {
      id: "welcome",
      title: "Welcome to Beanthentic",
      text: "Explore farmer profiles, history, and transactions from your dashboard.",
      time: "Just now",
      datetime: "2026-05-22",
      href: "/",
      kind: "info",
    },
    {
      id: "farmer-new",
      title: "New farmer registered",
      text: "A new farmer profile has been added to the system.",
      time: "1 day ago",
      datetime: "2026-05-21",
      href: "/farmer-profiles",
      kind: "farmer",
    },
    {
      id: "system-update",
      title: "System update",
      text: "Coffee history and report features are now available.",
      time: "2 days ago",
      datetime: "2026-05-20",
      href: "/report",
      kind: "info",
    },
  ];

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

    DEFAULT_NOTIFS.forEach((n) => {
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

  function resolveHref(notif) {
    if (notif.href) return notif.href;
    if (notif.kind === "transaction" && notif.reference_no) {
      return (
        "/transaction?ref=" + encodeURIComponent(String(notif.reference_no))
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

  function renderListItem(notif) {
    const li = document.createElement("li");
    li.className = "header-notif-item" + (notif.unread ? " is-unread" : "");
    li.setAttribute("data-notif-id", notif.id);
    if (notif.kind) li.setAttribute("data-notif-kind", notif.kind);
    const href = resolveHref(notif);
    if (href) li.setAttribute("data-notif-href", href);
    if (notif.reference_no) {
      li.setAttribute("data-notif-ref", String(notif.reference_no));
    }

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "header-notif-item-btn";
    btn.setAttribute("data-notif-open", "");
    if (href) {
      btn.setAttribute("aria-label", `${notif.title}. Go to related page.`);
    }

    btn.innerHTML =
      `<p class="header-notif-item-title">${escapeHtml(notif.title)}</p>` +
      `<p class="header-notif-item-text">${escapeHtml(notif.text)}</p>` +
      `<time class="header-notif-item-time" datetime="${escapeHtml(notif.datetime || "")}">${escapeHtml(notif.time || "")}</time>`;

    if (href) {
      const hint = document.createElement("span");
      hint.className = "header-notif-item-go";
      hint.textContent = "View →";
      btn.appendChild(hint);
    }

    li.appendChild(btn);
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
    push(notif) {
      const added = pushNotification(notif);
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
          `The farmer approved your transaction (Ref: ${ref}). Tap to open your transaction and receipt.`,
        time: "Just now",
        datetime: new Date().toISOString(),
        href: "/transaction?ref=" + encodeURIComponent(ref),
        reference_no: ref,
      });
      renderList();
      return added;
    },
    markAllRead() {
      markAllRead();
      renderList();
    },
    list: mergeNotifications,
    refresh: renderList,
  };

  uiRoot = document.querySelector("[data-notif-root]");
  if (!uiRoot) return;

  const toggle = uiRoot.querySelector("#notif-toggle");
  const panel = uiRoot.querySelector("#notif-panel");
  const markReadBtn = uiRoot.querySelector("[data-notif-mark-read]");
  const list = uiRoot.querySelector("[data-notif-list]");

  if (!toggle || !panel || !list) return;

  function setPanelOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    panel.classList.toggle("is-open", open);
    if (open) renderList();
  }

  function openNotification(item) {
    const id = item.getAttribute("data-notif-id");
    const href = item.getAttribute("data-notif-href") || "";
    if (id) markRead(id);
    item.classList.remove("is-unread");
    renderList();
    setPanelOpen(false);
    if (href) window.location.href = href;
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
    const btn = e.target.closest("[data-notif-open]");
    if (!btn) return;
    e.stopPropagation();
    const item = btn.closest(".header-notif-item");
    if (item) openNotification(item);
  });

  document.addEventListener("click", (e) => {
    if (!uiRoot.contains(e.target)) setPanelOpen(false);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setPanelOpen(false);
  });

  setPanelOpen(false);
  renderList();
})();
