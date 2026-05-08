(() => {
  const overlay = document.getElementById("report-chat-overlay");
  const dialog = document.getElementById("report-chat");
  const closeBtn = document.getElementById("report-chat-close");
  const form = document.getElementById("report-chat-form");
  const input = document.getElementById("report-chat-input");
  const body = document.getElementById("report-chat-body");

  if (!overlay || !dialog || !closeBtn || !form || !input || !body) return;

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

  function openChat() {
    overlay.hidden = false;
    dialog.hidden = false;
    document.body.classList.add("report-chat-open");
    if (!body.dataset.seeded) {
      addMessage("Hi! You can report a problem or send feedback here.", "bot");
      body.dataset.seeded = "1";
    }
    setTimeout(() => input.focus(), 0);
  }

  function closeChat() {
    overlay.hidden = true;
    dialog.hidden = true;
    document.body.classList.remove("report-chat-open");
  }

  document.querySelectorAll(".report-trigger").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      openChat();
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openChat();
      }
    });
  });

  overlay.addEventListener("click", closeChat);
  closeBtn.addEventListener("click", closeChat);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeChat();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const msg = input.value.trim();
    if (!msg) return;
    addMessage(msg, "user");
    input.value = "";
    setTimeout(() => botReply(msg), 250);
  });
})();

