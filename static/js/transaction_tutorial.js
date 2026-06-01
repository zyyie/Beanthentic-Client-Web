/**
 * Quick guide for homepage/menu transaction entry only (not farmer profile fill-up).
 */
(function () {
  "use strict";

  var HIGHLIGHT_CLASS = "txn-tutorial-highlight";
  var autoStarted = false;

  /* One screen: form overview + go to farmer profiles (no 8-step field tour). */
  var steps = [
    {
      target: null,
      title: "How to start a transaction",
      text: "This form is what you will fill out after you choose a farmer. Here is what you will need:",
      placement: "center",
      isOverview: true,
    },
  ];

  var root;
  var bubble;
  var arrowEl;
  var stepLabel;
  var titleEl;
  var textEl;
  var checklistEl;
  var finalBlock;
  var profileLink;
  var btnNext;
  var btnSkip;
  var index = 0;
  var activeHighlight = null;

  function cfg() {
    return window.BEANTHENTIC_TX_TUTORIAL || {};
  }

  function hasFarmerSelected() {
    try {
      var params = new URLSearchParams(window.location.search);
      var urlFid = parseInt(String(params.get("farmer_id") || 0), 10);
      if (urlFid > 0) return true;
    } catch (_p) { }
    var c = cfg();
    var fid = parseInt(String(c.farmerId || 0), 10);
    if (fid > 0) return true;
    var body = document.body;
    if (body && body.dataset) {
      var bodyFid = parseInt(String(body.dataset.farmerId || 0), 10);
      if (bodyFid > 0) return true;
    }
    return false;
  }

  function shouldShowTutorial() {
    return !hasFarmerSelected();
  }

  function updateTutorialUiForContext() {
    var showGuide = shouldShowTutorial();
    document.body.classList.toggle("transaction-page--from-farmer", !showGuide);
    var replayBanner = document.getElementById("txn-tutorial-replay");
    var replayInline = document.getElementById("txn-tutorial-replay-inline");
    if (replayBanner) replayBanner.hidden = !showGuide;
    if (replayInline) replayInline.hidden = !showGuide;
    if (!showGuide && root && !root.hidden) endTour();
  }

  function scheduleAutoStart() {
    if (autoStarted || !shouldShowTutorial()) return;
    window.setTimeout(function () {
      if (autoStarted || !root || !root.hidden || !shouldShowTutorial()) return;
      autoStarted = true;
      startTour();
    }, 650);
  }

  function clearHighlight() {
    if (activeHighlight) {
      activeHighlight.classList.remove(HIGHLIGHT_CLASS);
      activeHighlight = null;
    }
  }

  function getTargetEl(step) {
    if (!step || !step.target) return null;
    return document.querySelector(step.target);
  }

  function positionBubble(step, targetEl) {
    if (!bubble || !arrowEl) return;

    var gap = 14;
    var arrowSize = 12;
    var rect = { top: 0, left: 0, width: 0, height: 0 };
    var placement = step.placement || "bottom";

    if (targetEl) {
      rect = targetEl.getBoundingClientRect();
    } else if (placement === "center") {
      bubble.style.top = "50%";
      bubble.style.left = "50%";
      bubble.style.transform = "translate(-50%, -50%)";
      arrowEl.className = "txn-tutorial-arrow txn-tutorial-arrow--hidden";
      return;
    }

    bubble.style.transform = "none";
    var bubbleRect = bubble.getBoundingClientRect();
    var top = 0;
    var left = 0;

    if (placement === "top") {
      top = rect.top - bubbleRect.height - gap - arrowSize;
      left = rect.left + rect.width / 2 - bubbleRect.width / 2;
      arrowEl.className = "txn-tutorial-arrow txn-tutorial-arrow--bottom";
    } else {
      top = rect.bottom + gap + arrowSize;
      left = rect.left + rect.width / 2 - bubbleRect.width / 2;
      arrowEl.className = "txn-tutorial-arrow txn-tutorial-arrow--top";
    }

    var pad = 12;
    var maxLeft = window.innerWidth - bubbleRect.width - pad;
    left = Math.max(pad, Math.min(left, maxLeft));
    top = Math.max(pad, Math.min(top, window.innerHeight - bubbleRect.height - pad));

    bubble.style.top = top + "px";
    bubble.style.left = left + "px";

    var arrowLeft = rect.left + rect.width / 2 - left - arrowSize;
    arrowLeft = Math.max(16, Math.min(arrowLeft, bubbleRect.width - 32));
    arrowEl.style.left = arrowLeft + "px";
  }

  function renderStep() {
    var step = steps[index];
    if (!step) return endTour();

    clearHighlight();
    var targetEl = getTargetEl(step);
    if (targetEl) {
      targetEl.classList.add(HIGHLIGHT_CLASS);
      activeHighlight = targetEl;
      targetEl.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    }

    if (stepLabel) {
      stepLabel.textContent = step.isOverview ? "Quick guide" : "";
      stepLabel.hidden = !step.isOverview;
    }
    if (titleEl) {
      titleEl.textContent = step.title || "";
      titleEl.classList.remove("txn-tutorial-title--final");
    }
    if (textEl) {
      textEl.textContent = step.text || "";
      textEl.hidden = !step.text;
    }
    if (checklistEl) checklistEl.hidden = !step.isOverview;
    if (finalBlock) finalBlock.hidden = true;
    if (profileLink && cfg().farmerProfilesUrl) {
      profileLink.href = cfg().farmerProfilesUrl;
    }
    if (btnNext) btnNext.textContent = "Go to Farmer Profiles";
    if (bubble) {
      bubble.classList.toggle("txn-tutorial-bubble--overview", !!step.isOverview);
      bubble.classList.toggle("txn-tutorial-bubble--center", step.placement === "center");
      bubble.classList.remove("txn-tutorial-bubble--final");
    }
    if (arrowEl && step.placement === "center") {
      arrowEl.className = "txn-tutorial-arrow txn-tutorial-arrow--hidden";
    }

    window.requestAnimationFrame(function () {
      positionBubble(step, targetEl);
      window.requestAnimationFrame(function () {
        positionBubble(step, targetEl);
      });
    });
  }

  function startTour() {
    if (!root || !shouldShowTutorial()) return;
    index = 0;
    root.hidden = false;
    root.setAttribute("aria-hidden", "false");
    document.body.classList.add("txn-tutorial-active");
    renderStep();
  }

  function endTour() {
    clearHighlight();
    if (root) {
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
    }
    document.body.classList.remove("txn-tutorial-active");
  }

  function farmerProfilesUrl() {
    if (profileLink && profileLink.href) return profileLink.href;
    var url = cfg().farmerProfilesUrl;
    if (url) return url;
    return "/farmer-profiles";
  }

  function goToFarmerProfiles() {
    endTour();
    window.location.assign(farmerProfilesUrl());
  }

  function nextStep() {
    goToFarmerProfiles();
  }

  function onResize() {
    if (!root || root.hidden) return;
    renderStep();
  }

  function init() {
    root = document.getElementById("txn-tutorial");
    if (!root) return;

    bubble = root.querySelector(".txn-tutorial-bubble");
    arrowEl = root.querySelector(".txn-tutorial-arrow");
    stepLabel = document.getElementById("txn-tutorial-step-label");
    titleEl = document.getElementById("txn-tutorial-title");
    textEl = document.getElementById("txn-tutorial-text");
    checklistEl = document.getElementById("txn-tutorial-checklist");
    finalBlock = document.getElementById("txn-tutorial-final");
    profileLink = document.getElementById("txn-tutorial-profile-link");
    btnNext = document.getElementById("txn-tutorial-next");
    btnSkip = document.getElementById("txn-tutorial-skip");

    if (btnNext) btnNext.addEventListener("click", nextStep);
    if (btnSkip) btnSkip.addEventListener("click", endTour);

    root.querySelectorAll("[data-txn-tutorial-dismiss]").forEach(function (el) {
      el.addEventListener("click", endTour);
    });

    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);

    function bindReplay(btn) {
      if (!btn) return;
      btn.addEventListener("click", function () {
        startTour();
      });
    }
    bindReplay(document.getElementById("txn-tutorial-replay"));
    bindReplay(document.getElementById("txn-tutorial-replay-inline"));

    updateTutorialUiForContext();
    scheduleAutoStart();
    window.addEventListener("load", function () {
      updateTutorialUiForContext();
      if (!autoStarted) scheduleAutoStart();
    });

    window.BeanthenticTxTutorial = {
      start: startTour,
      end: endTour,
      shouldShow: shouldShowTutorial,
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
