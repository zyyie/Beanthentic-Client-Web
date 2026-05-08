(() => {
  const reasonButtons = Array.from(document.querySelectorAll(".report-reason"));
  const details = document.getElementById("report-details");
  const detailsTitle = document.getElementById("report-details-title");
  const resetBtn = document.getElementById("report-details-reset");
  const quickOptions = document.getElementById("report-quick-options");
  const form = document.getElementById("report-chat-form");
  const input = document.getElementById("report-chat-input");
  const body = document.getElementById("report-chat-body");

  if (!form || !input || !body || !details || !detailsTitle || !resetBtn || !quickOptions) return;

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

  function addMessage(text, who) {
    const row = document.createElement("div");
    row.className = `report-chat-row ${who}`;

    const bubble = document.createElement("div");
    bubble.className = "report-chat-bubble";
    bubble.textContent = text;

    row.appendChild(bubble);
    body.appendChild(row);
    body.scrollTop = body.scrollHeight;
  }

  function botReply(userText) {
    const t = (userText || "").trim().toLowerCase();
    if (!t) return;

    if (t.includes("bug") || t.includes("error") || t.includes("issue")) {
      addMessage(
        "Thanks! Please describe what happened and what you expected to happen. You can also paste the steps to reproduce.",
        "bot"
      );
      return;
    }

    if (t.includes("account") || t.includes("login") || t.includes("sign")) {
      addMessage(
        "Account concern noted. Tell me your email/username and what part you can't access (login, register, profile).",
        "bot"
      );
      return;
    }

    addMessage(
      "Got it. Please provide details (what page, what you did, and any screenshot) so we can record your report.",
      "bot"
    );
  }

  function setTypingEnabled(enabled) {
    input.disabled = !enabled;
    form.querySelector(".report-chat-send").disabled = !enabled;
    form.style.opacity = enabled ? "1" : "0.55";
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
        addMessage(label, "user");
        addMessage(
          "Thanks! If you want to add details (date, seller, proof), you can choose 'Something else' or change reason.",
          "bot"
        );
      });
      row.appendChild(b);
    });

    quickOptions.appendChild(row);
    setTypingEnabled(false);
  }

  function openDetails(reason) {
    details.hidden = false;
    detailsTitle.textContent = reason;
    body.innerHTML = "";
    addMessage("Thanks. Please describe your report in detail.", "bot");
    addMessage(`Selected reason: ${reason}`, "bot");
    setTimeout(() => input.focus(), 0);
  }

  function resetDetails() {
    details.hidden = true;
    detailsTitle.textContent = "Report details";
    body.innerHTML = "";
    input.value = "";
    quickOptions.innerHTML = "";
    setTypingEnabled(false);
  }

  reasonButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const reason = btn.getAttribute("data-reason") || "Report";
      openDetails(reason);

      const isSomethingElse = btn.getAttribute("data-something-else") === "true";
      input.placeholder = isSomethingElse ? "Type what you want to report…" : "Type details…";
      renderQuickOptions(reason, isSomethingElse);
    });
  });

  resetBtn.addEventListener("click", resetDetails);

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (input.disabled) return;
    const msg = input.value.trim();
    if (!msg) return;
    addMessage(msg, "user");
    input.value = "";
    setTimeout(() => botReply(msg), 250);
  });

  // Default: no typing until a reason is selected.
  setTypingEnabled(false);
})();

