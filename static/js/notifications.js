(function () {
  const root = document.querySelector("[data-notif-root]");
  if (!root) return;

  const toggle = root.querySelector("#notif-toggle");
  const panel = root.querySelector("#notif-panel");
  const badge = root.querySelector("[data-notif-badge]");
  const markReadBtn = root.querySelector("[data-notif-mark-read]");
  const list = root.querySelector("[data-notif-list]");
  const empty = root.querySelector("[data-notif-empty]");

  if (!toggle || !panel) return;

  function unreadCount() {
    return root.querySelectorAll(".header-notif-item.is-unread").length;
  }

  function updateBadge() {
    const count = unreadCount();
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count > 9 ? "9+" : String(count);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }

  function setPanelOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      panel.classList.add("is-open");
    } else {
      panel.classList.remove("is-open");
    }
  }

  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    setPanelOpen(panel.hasAttribute("hidden"));
  });

  markReadBtn?.addEventListener("click", () => {
    root.querySelectorAll(".header-notif-item.is-unread").forEach((item) => {
      item.classList.remove("is-unread");
    });
    updateBadge();
    if (list) list.hidden = true;
    if (empty) empty.hidden = false;
  });

  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) {
      setPanelOpen(false);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      setPanelOpen(false);
    }
  });

  setPanelOpen(false);
  updateBadge();
})();
