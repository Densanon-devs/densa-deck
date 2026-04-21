// First-run tour — declarative 7-step walkthrough.
//
// Each step describes WHICH element to spotlight, which tab to switch to
// first (so the element is actually rendered), and what copy to show in the
// tooltip. The positioning logic places the card next to the spotlight on
// whichever side has the most room, falling back to a centered modal when
// no target element is visible (e.g. the welcome + completion steps).
//
// Usage: exported window.Tour = { start(), restart(), isCompleted() }.
// app.js calls Tour.start() after bootstrap if the first-run flag isn't set.

(function () {
  const STEPS = [
    {
      id: "welcome",
      title: "Welcome to Densa Deck",
      body: "A quick 30-second tour of how to use the app. You can skip this anytime from the top-right of the overlay, and replay it later from Settings → About.",
      // No target -> centered modal style
    },
    {
      id: "setup-db",
      title: "Install the card database first",
      body: "Before anything else, click <strong>Install card database</strong> to pull ~250 MB of Scryfall data. This only happens once and makes everything else offline.",
      tab: "settings",
      target: "#ingest-btn",
    },
    {
      id: "paste-deck",
      title: "Paste a decklist",
      body: "One card per line. Section headers like <code>Commander:</code> or <code>Mainboard:</code> are recognized. Or paste a Moxfield/Archidekt URL in the row above to import automatically.",
      tab: "analyze",
      target: "#decklist-input",
    },
    {
      id: "analyze",
      title: "Run the analysis",
      body: "Click <strong>Analyze</strong> to get mana curve, power level, color source analysis, staples check, and castability warnings. Free tier includes this — Pro unlocks the deep-dive features.",
      tab: "analyze",
      target: "#analyze-btn",
    },
    {
      id: "save",
      title: "Save decks for later (Pro)",
      body: "Click <strong>Save a version</strong> to track your deck over time. Edit the deck later, save again, and <strong>My Decks</strong> shows a full diff history — score deltas, added/removed cards, per-version notes.",
      tab: "analyze",
      target: "#save-btn",
    },
    {
      id: "simulate",
      title: "Simulate games (Pro)",
      body: "Goldfish runs thousands of solo games to estimate kill turn, mulligan rates, and consistency. Gauntlet tests your deck against 11 archetypes and returns win-rate breakdowns.",
      tab: "analyze",
      target: "#goldfish-btn",
    },
    {
      id: "coach",
      title: "Talk to the AI coach (Pro)",
      body: "The <strong>Coach</strong> tab opens a conversation bound to one of your saved decks. It reads your structured analysis and answers free-form questions — <em>and</em> it cannot name cards that aren't in your deck, which rules out the usual AI card-invention problem.",
      tab: "coach",
      target: '.tab-btn[data-view="coach"]',
    },
    {
      id: "done",
      title: "You're all set",
      body: "Your purchase + license activation flow is in <strong>Settings</strong>. Free tier works forever; Pro unlocks save, goldfish, gauntlet, coach, and export. Have fun deckbuilding.",
      // Centered modal, no target
    },
  ];

  const els = {};
  let currentIdx = 0;
  let repositionListener = null;

  function $(id) { return document.getElementById(id); }

  function cacheElements() {
    els.overlay = $("tour-overlay");
    els.spotlight = $("tour-spotlight");
    els.card = $("tour-card");
    els.title = $("tour-title");
    els.body = $("tour-body");
    els.stepIndex = $("tour-step-index");
    els.back = $("tour-back-btn");
    els.next = $("tour-next-btn");
    els.skip = $("tour-skip-btn");
  }

  async function start() {
    if (!els.overlay) cacheElements();
    currentIdx = 0;
    // Clean up any stale listener from a prior tour pass so a restart
    // doesn't leak window event handlers.
    if (repositionListener) {
      window.removeEventListener("resize", repositionListener);
      repositionListener = null;
    }
    renderStep();
    els.overlay.classList.remove("hidden");
    els.overlay.setAttribute("aria-hidden", "false");
    // Re-measure on window resize so the spotlight stays aligned when
    // the user resizes or we scroll a tab with different layout
    repositionListener = () => { if (!els.overlay.classList.contains("hidden")) renderStep(); };
    window.addEventListener("resize", repositionListener);
  }

  async function complete(skipped) {
    els.overlay.classList.add("hidden");
    els.overlay.setAttribute("aria-hidden", "true");
    if (repositionListener) {
      window.removeEventListener("resize", repositionListener);
      repositionListener = null;
    }
    // Mark done in app state — we treat skip and finish the same.
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.mark_first_run_complete) {
        await window.pywebview.api.mark_first_run_complete();
      }
    } catch (e) { /* non-fatal */ }
  }

  function renderStep() {
    const step = STEPS[currentIdx];
    els.title.textContent = step.title;
    els.body.innerHTML = step.body;
    els.stepIndex.textContent = `${currentIdx + 1} / ${STEPS.length}`;
    els.back.disabled = currentIdx === 0;
    els.next.textContent = currentIdx === STEPS.length - 1 ? "Finish" : "Next";

    // Switch tab first (if specified) so the target element is in the DOM
    // with its real layout before we measure.
    if (step.tab && window.__tourSwitchView) {
      window.__tourSwitchView(step.tab);
    }

    // Position after a tick so the tab switch's reflow settles
    setTimeout(() => positionForStep(step), 30);
  }

  function positionForStep(step) {
    if (!step.target) {
      els.overlay.classList.add("centered");
      return;
    }
    const target = document.querySelector(step.target);
    if (!target) {
      // Missing element -> degrade gracefully to centered modal
      els.overlay.classList.add("centered");
      return;
    }

    els.overlay.classList.remove("centered");

    // Scroll target into view if needed, then compute the spotlight rect
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    const rect = target.getBoundingClientRect();

    // Zero-size rect means the target is hidden (display:none parent, or
    // still mid-tab-switch). Degrade to centered modal so we don't plant
    // a spotlight at coordinates (0,0) with 0 width.
    if (rect.width === 0 || rect.height === 0) {
      els.overlay.classList.add("centered");
      return;
    }

    // Pad the spotlight by 8px so the highlight "breathes" around the element
    const pad = 8;
    els.spotlight.style.top = (rect.top - pad) + "px";
    els.spotlight.style.left = (rect.left - pad) + "px";
    els.spotlight.style.width = (rect.width + pad * 2) + "px";
    els.spotlight.style.height = (rect.height + pad * 2) + "px";

    // Place the card wherever there's more room — below by default,
    // above when target is in the bottom third, left/right when the card
    // would overflow off-screen otherwise.
    const cardW = 360;
    const cardH = els.card.offsetHeight || 200;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const gap = 16;

    let top, left;
    // Prefer below, then above, then right, then left of the target
    if (rect.bottom + cardH + gap < vh) {
      top = rect.bottom + gap;
      left = Math.min(Math.max(rect.left, 20), vw - cardW - 20);
    } else if (rect.top - cardH - gap > 0) {
      top = rect.top - cardH - gap;
      left = Math.min(Math.max(rect.left, 20), vw - cardW - 20);
    } else if (rect.right + cardW + gap < vw) {
      top = Math.min(Math.max(rect.top, 20), vh - cardH - 20);
      left = rect.right + gap;
    } else {
      top = Math.min(Math.max(rect.top, 20), vh - cardH - 20);
      left = Math.max(20, rect.left - cardW - gap);
    }
    els.card.style.top = top + "px";
    els.card.style.left = left + "px";
  }

  function next() {
    if (currentIdx >= STEPS.length - 1) {
      complete(false);
    } else {
      currentIdx += 1;
      renderStep();
    }
  }

  function back() {
    if (currentIdx > 0) {
      currentIdx -= 1;
      renderStep();
    }
  }

  function wire() {
    cacheElements();
    if (!els.overlay) return;
    els.next.addEventListener("click", next);
    els.back.addEventListener("click", back);
    els.skip.addEventListener("click", () => complete(true));
    // Escape also skips
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !els.overlay.classList.contains("hidden")) {
        complete(true);
      }
    });
  }

  async function maybeStartOnFirstLaunch() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const r = await window.pywebview.api.get_first_run_state();
      const data = r && r.data ? r.data : r;
      if (!data || !data.completed) {
        start();
      }
    } catch (e) { /* non-fatal */ }
  }

  async function restart() {
    if (!window.pywebview || !window.pywebview.api) { start(); return; }
    try {
      await window.pywebview.api.reset_first_run();
    } catch (e) { /* non-fatal */ }
    start();
  }

  window.Tour = {
    wire, start, restart, maybeStartOnFirstLaunch,
  };
})();
