(function () {
  const LOADING_MS = 2000;
  let busy = false;
  let pendingTimer = null;

  function getOverlay() {
    return document.getElementById("beanthentic-page-loading");
  }

  function showLoading() {
    const overlay = getOverlay();
    if (!overlay) return;
    overlay.hidden = false;
    overlay.classList.add("is-visible");
    document.body.classList.add("page-loading-active");
    busy = true;
  }

  function hideLoading() {
    const overlay = getOverlay();
    if (overlay) {
      overlay.hidden = true;
      overlay.classList.remove("is-visible");
    }
    document.body.classList.remove("page-loading-active");
    busy = false;
    if (pendingTimer) {
      window.clearTimeout(pendingTimer);
      pendingTimer = null;
    }
  }

  function shouldSkip(el) {
    if (!el || busy) return true;
    if (el.closest("[data-no-page-loading]")) return true;
    if (el.closest("#beanthentic-page-loading")) return true;
    if (el.matches("#notif-toggle, [data-notif-mark-read]")) return true;
    if (el.closest(".report-chat") && !el.closest("a[href]")) return true;
    if (
      el.closest(".header-notif-panel") &&
      !el.closest("a[href].header-notif-item-card, a[data-notif-view]")
    ) {
      return true;
    }
    if (el.matches("input, textarea, select, label")) return true;
    return false;
  }

  function isNavigableHref(href) {
    const raw = String(href || "").trim();
    if (!raw || raw === "#" || /^javascript:/i.test(raw)) return false;
    try {
      const url = new URL(raw, window.location.href);
      if (
        url.origin === window.location.origin &&
        url.pathname === window.location.pathname &&
        url.hash &&
        !url.search
      ) {
        return false;
      }
      return true;
    } catch {
      return false;
    }
  }

  function isTransactionNavLink(link) {
    if (!link) return false;
    if (
      link.classList.contains("transaction-card") ||
      link.classList.contains("pi-transaction-link") ||
      link.hasAttribute("data-transaction-url")
    ) {
      return true;
    }
    try {
      const href = link.getAttribute("href") || link.href || "";
      const path = new URL(href, window.location.href).pathname.replace(/\/+$/, "") || "/";
      return path === "/transaction" || path.endsWith("/transaction");
    } catch {
      return false;
    }
  }

  function beforeNavigate(link) {
    if (!link) return;

    if (isTransactionNavLink(link)) {
      try {
        sessionStorage.setItem("beanthentic_tx_pending_guide", "1");
      } catch (_guide) {
        /* ignore */
      }
    }

    if (
      !link.classList.contains("pi-transaction-link") &&
      !link.hasAttribute("data-transaction-url")
    ) {
      return;
    }
    try {
      sessionStorage.setItem(
        "beanthentic_tx_farmer_context",
        JSON.stringify({
          farmer_id: link.getAttribute("data-farmer-id") || "",
          farmer_name: link.getAttribute("data-farmer-name") || "",
        })
      );
    } catch (_e) {
      /* ignore */
    }
  }

  function scheduleAction(action) {
    showLoading();
    pendingTimer = window.setTimeout(function () {
      pendingTimer = null;
      action();
    }, LOADING_MS);
  }

  document.addEventListener(
    "click",
    function (e) {
      if (e.defaultPrevented) return;

      const link = e.target.closest("a[href]");
      if (link && !shouldSkip(link)) {
        const href = link.getAttribute("href");
        if (!isNavigableHref(href)) return;

        e.preventDefault();
        e.stopImmediatePropagation();

        const dest = link.href;
        const newTab = link.target === "_blank";

        beforeNavigate(link);

        scheduleAction(function () {
          if (newTab) {
            window.open(dest, "_blank", "noopener,noreferrer");
            hideLoading();
            return;
          }
          window.location.assign(dest);
        });
      }
    },
    true
  );

  window.addEventListener("pageshow", function (e) {
    if (e.persisted) hideLoading();
  });

  window.BeanthenticPageLoading = {
    show: showLoading,
    hide: hideLoading,
    durationMs: LOADING_MS,
  };
})();
