(() => {
  const reasonButtons = Array.from(document.querySelectorAll(".report-reason"));
  const details = document.getElementById("report-details");
  const detailsTitle = document.getElementById("report-details-title");
  const resetBtn = document.getElementById("report-details-reset");
  const quickOptions = document.getElementById("report-quick-options");
  const form = document.getElementById("report-chat-form");
  const input = document.getElementById("report-chat-input");
  const body = document.getElementById("report-chat-body");
  const reporterNameEl = document.getElementById("report-reporter-name");
  const reporterContactEl = document.getElementById("report-reporter-contact");
  const farmerSelectEl = document.getElementById("report-farmer-select");

  if (!form || !input || !body || !details || !detailsTitle || !resetBtn || !quickOptions) return;

  const reasonsList = document.querySelector(".report-reasons");
  const STORAGE_KEY = "beanthentic_client_pending_tx";
  const SUBMIT_URL =
    window.BEANTHENTIC_REPORT_SUBMIT_URL || "/api/client-report/submit";
  const FARMERS_URL =
    window.BEANTHENTIC_REPORT_FARMERS_URL || "/api/client-report/transaction-farmers";

  const state = {
    reason: "",
    detail: "",
    isSomethingElse: false,
    chatLog: [],
    submitted: false,
    submitting: false,
    farmers: [],
    farmersLoading: false,
  };

  let farmersLoadTimer = null;

  const PRESET_OPTIONS = {
    "Overcharged or unfair pricing": [
      "Overcharged vs agreed price",
      "Unexpected extra fees",
      "Wrong computation/receipt",
    ],
    "Poor quality coffee beans": [
      "Stale/old beans",
      "Spoiled/moldy beans",
      "Not as described",
    ],
    "Incomplete or incorrect amount": [
      "Wrong weight",
      "Wrong bean type",
      "Missing items",
    ],
    "Fake or not authentic product": [
      "Fake origin claim",
      "Suspected counterfeit",
      "Mismatch in certification/label",
    ],
    "Rude or unprofessional behavior": [
      "Rude messages",
      "Disrespectful behavior",
      "Unhelpful support",
    ],
    "Refused refund or exchange": [
      "Refused refund request",
      "Refused replacement",
      "No response to refund request",
    ],
  };

  function readTxStorage() {
    try {
      const raw =
        sessionStorage.getItem(STORAGE_KEY) || localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function prefillReporterFields() {
    const tx = readTxStorage();
    if (reporterNameEl && !reporterNameEl.value.trim()) {
      const name =
        (tx && (tx.client_name || tx.buyer || tx.buyer_name)) || "";
      if (name) reporterNameEl.value = String(name).trim();
    }
    scheduleLoadTransactionFarmers();
  }

  function setFarmerSelectMessage(message, disabled = true) {
    if (!farmerSelectEl) return;
    farmerSelectEl.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = message;
    farmerSelectEl.appendChild(opt);
    farmerSelectEl.disabled = disabled;
  }

  function renderFarmerOptions(farmers) {
    if (!farmerSelectEl) return;
    state.farmers = Array.isArray(farmers) ? farmers : [];
    farmerSelectEl.innerHTML = "";

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent =
      state.farmers.length === 1
        ? "Confirm farmer"
        : "Select farmer you transacted with…";
    farmerSelectEl.appendChild(placeholder);

    state.farmers.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = String(f.farmer_id || "");
      const txNote =
        f.tx_count > 1 ? ` · ${f.tx_count} transactions` : " · 1 transaction";
      opt.textContent = `${f.farmer_name || "Farmer"}${txNote}`;
      opt.dataset.farmerName = f.farmer_name || "";
      opt.dataset.farmerNo = f.farmer_no || "";
      farmerSelectEl.appendChild(opt);
    });

    farmerSelectEl.disabled = state.farmers.length === 0;
    if (state.farmers.length === 1) {
      farmerSelectEl.value = String(state.farmers[0].farmer_id);
    }
  }

  async function loadTransactionFarmers(clientName) {
    const name = (clientName || "").trim();
    if (!farmerSelectEl) return;

    if (!name) {
      setFarmerSelectMessage("Enter your name above to load farmers…", true);
      state.farmers = [];
      return;
    }

    if (state.farmersLoading) return;
    state.farmersLoading = true;
    setFarmerSelectMessage("Loading farmers from your transactions…", true);

    try {
      const url =
        FARMERS_URL + "?client_name=" + encodeURIComponent(name);
      const res = await fetch(url);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      const list = Array.isArray(data.farmers) ? data.farmers : [];
      if (!list.length) {
        setFarmerSelectMessage(
          "No transactions found for this name. Use the same name as on your order.",
          true
        );
        state.farmers = [];
        return;
      }
      renderFarmerOptions(list);
    } catch (err) {
      setFarmerSelectMessage(
        `Could not load farmers (${err.message || "error"}). Check connection.`,
        true
      );
      state.farmers = [];
    } finally {
      state.farmersLoading = false;
    }
  }

  function scheduleLoadTransactionFarmers() {
    if (!reporterNameEl) return;
    clearTimeout(farmersLoadTimer);
    farmersLoadTimer = setTimeout(() => {
      loadTransactionFarmers(reporterNameEl.value.trim());
    }, 400);
  }

  function selectedFarmerPayload() {
    if (!farmerSelectEl) return null;
    const fid = parseInt(farmerSelectEl.value || "0", 10);
    if (fid <= 0) return null;
    const opt = farmerSelectEl.selectedOptions[0];
    const match = state.farmers.find((f) => Number(f.farmer_id) === fid);
    return {
      farmer_id: fid,
      farmer_name: (opt && opt.dataset.farmerName) || (match && match.farmer_name) || "",
      farmer_no: (opt && opt.dataset.farmerNo) || (match && match.farmer_no) || "",
    };
  }

  function addMessage(text, who) {
    const row = document.createElement("div");
    row.className = `report-chat-row ${who}`;

    const bubble = document.createElement("div");
    bubble.className = "report-chat-bubble";
    bubble.textContent = text;

    row.appendChild(bubble);
    body.appendChild(row);
    body.scrollTop = body.scrollHeight;

    if (text && who) {
      state.chatLog.push({ who, text: String(text) });
    }
  }

  function buildAllegation() {
    const parts = [];
    if (state.reason) parts.push(`Category: ${state.reason}`);
    if (state.detail) parts.push(`Detail: ${state.detail}`);
    const userLines = state.chatLog
      .filter((m) => m.who === "user")
      .map((m) => m.text.trim())
      .filter(Boolean);
    if (userLines.length) {
      parts.push(`Details:\n${userLines.join("\n")}`);
    }
    return parts.join("\n\n").trim();
  }

  function canSubmit() {
    if (state.submitted || state.submitting) return false;
    if (!state.reason) return false;
    const allegation = buildAllegation();
    if (!allegation) return false;
    if (!state.isSomethingElse && !state.detail) {
      const hasUserText = state.chatLog.some((m) => m.who === "user");
      if (!hasUserText) return false;
    }
    return true;
  }

  async function submitReport() {
    if (!canSubmit()) return false;

    const reporterName = (reporterNameEl && reporterNameEl.value.trim()) || "";
    if (!reporterName) {
      addMessage("Please enter your full name above before sending.", "bot");
      if (reporterNameEl) reporterNameEl.focus();
      return false;
    }

    const farmer = selectedFarmerPayload();
    if (!farmer) {
      addMessage(
        "Please select the farmer you transacted with from the list above.",
        "bot"
      );
      if (farmerSelectEl) farmerSelectEl.focus();
      return false;
    }

    state.submitting = true;
    const sendBtn = form.querySelector(".report-chat-send");
    if (sendBtn) sendBtn.disabled = true;
    addMessage("Submitting your report…", "bot");

    const payload = {
      reporter_name: reporterName,
      reporter_contact:
        (reporterContactEl && reporterContactEl.value.trim()) || "",
      reason_category: state.reason,
      reason_detail: state.detail || "",
      allegation: buildAllegation(),
      chat_log: state.chatLog,
      farmer_id: farmer.farmer_id,
      farmer_name: farmer.farmer_name,
      farmer_no: farmer.farmer_no,
    };

    try {
      const res = await fetch(SUBMIT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      state.submitted = true;
      body.lastElementChild?.remove();
      addMessage(
        data.message ||
          "Your report was submitted. Our team will review it. Thank you.",
        "bot"
      );
      setTypingEnabled(false);
      return true;
    } catch (err) {
      body.lastElementChild?.remove();
      addMessage(
        `Could not save your report (${err.message || "error"}). Check connection and try again.`,
        "bot"
      );
      return false;
    } finally {
      state.submitting = false;
      if (!state.submitted && sendBtn) sendBtn.disabled = input.disabled;
    }
  }

  function setTypingEnabled(enabled) {
    const on = enabled && !state.submitted;
    input.disabled = !on;
    const sendBtn = form.querySelector(".report-chat-send");
    if (sendBtn) sendBtn.disabled = !on;
    form.style.opacity = on ? "1" : "0.55";
  }

  function renderQuickOptions(reason, isSomethingElse) {
    quickOptions.innerHTML = "";

    if (isSomethingElse) {
      setTypingEnabled(true);
      return;
    }

    const opts = PRESET_OPTIONS[reason] || ["Option 1", "Option 2", "Option 3"];

    const row = document.createElement("div");
    row.className = "report-quick-buttons";

    opts.forEach((label) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "report-quick-btn";
      b.textContent = label;
      b.addEventListener("click", () => {
        state.detail = label;
        document.querySelectorAll(".report-quick-btn").forEach((el) => {
          el.classList.toggle("is-selected", el === b);
        });
        addMessage(label, "user");
        addMessage(
          "Thanks. Add any extra details below (date, amount, proof), then press Send to submit your report.",
          "bot"
        );
        setTypingEnabled(true);
        input.focus();
      });
      row.appendChild(b);
    });

    quickOptions.appendChild(row);
    setTypingEnabled(false);
  }

  function setSelectedReason(activeBtn) {
    reasonButtons.forEach((b) => b.classList.remove("is-selected"));
    if (activeBtn) activeBtn.classList.add("is-selected");
  }

  function placeDetailsAfter(button) {
    if (!button || !details) return;
    button.insertAdjacentElement("afterend", details);
  }

  function parkDetailsAtEnd() {
    if (!reasonsList || !details) return;
    reasonsList.insertAdjacentElement("afterend", details);
  }

  function openDetails(reason, button) {
    state.reason = reason;
    state.detail = "";
    state.isSomethingElse = button.getAttribute("data-something-else") === "true";
    state.chatLog = [];
    state.submitted = false;
    state.submitting = false;

    placeDetailsAfter(button);
    setSelectedReason(button);
    details.hidden = false;
    detailsTitle.textContent = reason;
    body.innerHTML = "";
    prefillReporterFields();
    addMessage("Thanks. Please describe your report in detail.", "bot");
    addMessage(`Selected reason: ${reason}`, "bot");
    setTimeout(() => {
      details.scrollIntoView({ block: "nearest", behavior: "smooth" });
      if (reporterNameEl && !reporterNameEl.value.trim()) {
        reporterNameEl.focus();
      } else {
        input.focus();
      }
    }, 0);
  }

  function resetDetails() {
    details.hidden = true;
    setSelectedReason(null);
    parkDetailsAtEnd();
    detailsTitle.textContent = "Report details";
    body.innerHTML = "";
    input.value = "";
    quickOptions.innerHTML = "";
    state.reason = "";
    state.detail = "";
    state.isSomethingElse = false;
    state.chatLog = [];
    state.submitted = false;
    state.submitting = false;
    state.farmers = [];
    setFarmerSelectMessage("Enter your name above to load farmers…", true);
    setTypingEnabled(false);
  }

  if (reporterNameEl) {
    reporterNameEl.addEventListener("input", scheduleLoadTransactionFarmers);
    reporterNameEl.addEventListener("blur", () => {
      clearTimeout(farmersLoadTimer);
      loadTransactionFarmers(reporterNameEl.value.trim());
    });
  }

  reasonButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const reason = btn.getAttribute("data-reason") || "Report";
      openDetails(reason, btn);

      const isSomethingElse = btn.getAttribute("data-something-else") === "true";
      input.placeholder = isSomethingElse
        ? "Type what you want to report…"
        : "Type details…";
      renderQuickOptions(reason, isSomethingElse);
    });
  });

  resetBtn.addEventListener("click", resetDetails);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (input.disabled || state.submitted) return;
    const msg = input.value.trim();

    if (!msg) {
      if (canSubmit()) await submitReport();
      return;
    }

    if (state.isSomethingElse && !state.detail) {
      state.detail = msg;
    }

    addMessage(msg, "user");
    input.value = "";

    if (canSubmit()) {
      await submitReport();
      return;
    }

    if (!state.isSomethingElse && !state.detail) {
      addMessage("Please pick one of the options above, then add details if needed.", "bot");
      return;
    }

    addMessage("Press Send again to submit your report, or add more details first.", "bot");
  });

  prefillReporterFields();
  if (!reporterNameEl || !reporterNameEl.value.trim()) {
    setFarmerSelectMessage("Enter your name above to load farmers…", true);
  }
  setTypingEnabled(false);
})();
