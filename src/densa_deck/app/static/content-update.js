/* Densa Deck — one-click content upkeep.

   Principle: the app already knows when its data is missing, stale, or
   half-finished. Making the user notice that, work out which of four
   downloads applies, and click the right button is our job leaking into
   theirs. One banner, one button, everything.

   Runs automatically on launch. It only ever *offers* — nothing downloads
   without the click, because these are large transfers on someone else's
   connection. */
(function () {
  "use strict";

  const state = { wired: false, polling: false };
  function e(id) { return document.getElementById(id); }

  async function check() {
    wireOnce();
    let s;
    try {
      s = await callApi("get_content_status");
    } catch (err) {
      return;   // never let upkeep noise block the app starting
    }
    render(s);
  }

  function render(s) {
    const banner = e("content-banner");
    const body = e("content-banner-body");
    if (!banner || !body) return;

    if (!s.count) {
      banner.classList.add("hidden");
      return;
    }

    const labels = s.items.map(i => i.label);
    const required = s.items.filter(i => i.severity === "required");
    const size = s.total_mb ? ` (~${Math.round(s.total_mb)} MB)` : "";

    // Say what and why in one line — the detail is in the title attribute
    // for anyone who wants it, not stacked in front of everyone.
    const headline = required.length
      ? `<strong>Setup needed:</strong> ${escape(joinNicely(labels))} ${
          labels.length === 1 ? "is" : "are"} missing or out of date${size}.`
      : `<strong>Updates available:</strong> ${escape(joinNicely(labels))}${size}.`;

    body.innerHTML = headline +
      ` <span class="subtle" title="${escape(s.items.map(
          i => i.label + ": " + i.detail).join(" · "))}">Details</span>`;
    banner.classList.remove("hidden");
  }

  function joinNicely(list) {
    if (list.length <= 1) return list[0] || "";
    if (list.length === 2) return list[0] + " and " + list[1];
    return list.slice(0, -1).join(", ") + " and " + list[list.length - 1];
  }

  async function updateEverything() {
    const btn = e("content-update-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Updating…"; }
    e("content-banner").classList.add("hidden");
    e("content-progress").classList.remove("hidden");
    e("content-progress-body").textContent = "Starting…";

    try {
      const r = await callApi("update_all_content_start");
      if (r && r.ok === false) {
        e("content-progress-body").textContent = r.error;
        return;
      }
    } catch (err) {
      e("content-progress-body").textContent = "Could not start: " + err.message;
      return;
    }
    poll();
  }

  async function poll() {
    if (state.polling) return;
    state.polling = true;
    const tick = async () => {
      let p;
      try {
        p = await callApi("update_all_content_progress");
      } catch (err) {
        state.polling = false;
        return;
      }
      const body = e("content-progress-body");
      if (body) body.textContent = `${p.pct || 0}% — ${p.message || ""}`;

      if (!p.done) { setTimeout(tick, 700); return; }

      state.polling = false;
      const btn = e("content-update-btn");
      if (btn) { btn.disabled = false; btn.textContent = "Update everything"; }

      if (p.error) {
        if (body) body.textContent = "Update failed: " + p.error;
        return;
      }
      if (typeof toast === "function") toast(p.message, "success");
      // Fade the progress line out, then re-check so anything that only
      // partially completed re-offers itself rather than going quiet.
      setTimeout(() => {
        e("content-progress").classList.add("hidden");
        check();
        // Views that render this data should pick it up without a restart.
        if (window.__collectionActivate &&
            document.getElementById("view-collection").classList.contains("active")) {
          window.__collectionActivate();
        }
      }, 2500);
    };
    tick();
  }

  function wireOnce() {
    if (state.wired) return;
    const btn = e("content-update-btn");
    if (!btn) return;
    state.wired = true;
    btn.addEventListener("click", updateEverything);
  }

  window.__contentCheck = check;

  // Auto-flag on launch. Deferred so it never competes with first paint.
  window.addEventListener("DOMContentLoaded", () => setTimeout(check, 1200));
})();
