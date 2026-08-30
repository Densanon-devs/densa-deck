/* Densa Deck — Scan view.

   Turns a card in your hand into a row in your collection. Three inputs, all
   producing the same downstream flow: type a name, paste the bottom-left
   corner, or capture a frame from a camera.

   The rule that shapes this whole file: nothing is ever added silently
   unless the match is certain. A wrong card in someone's inventory is worse
   than no card, because they won't know to go looking for it. Anything below
   "certain" renders a picker and waits.

   IIFE like builder.js/collection.js; uses app.js's globals. */
(function () {
  "use strict";

  const state = {
    wired: false,
    caps: null,
    lastResult: null,
    busy: false,
  };

  function e(id) { return document.getElementById(id); }
  function money(v) {
    return (v === null || v === undefined) ? "—" : "$" + Number(v).toFixed(2);
  }

  // ---------------------------------------------------------- capabilities

  async function refreshCapabilities() {
    let caps;
    try {
      caps = await callApi("get_scan_capabilities");
    } catch (err) {
      return;
    }
    state.caps = caps;

    const note = e("scan-capability-note");
    const camBtn = e("scan-camera-btn");
    if (!note) return;

    if (!caps.catalogue_ready) {
      note.innerHTML = "Printing data isn't installed, so cards can't be " +
        "identified yet. <a href=\"#\" id=\"scan-goto-collection\">Download it</a> " +
        "from the Collection tab.";
      const link = e("scan-goto-collection");
      if (link) link.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (window.__tourSwitchView) window.__tourSwitchView("collection");
        document.querySelector('.tab-btn[data-view="collection"]').click();
      });
      return;
    }

    // Report honestly rather than optimistically — camera capture needs an
    // optional dependency we deliberately don't bundle.
    const cam = caps.camera || {};
    const ocr = (caps.ocr_backends || []).find(b => b.available);
    if (cam.available && ocr) {
      note.textContent = `Camera ready (OCR: ${ocr.name}).`;
      if (camBtn) camBtn.disabled = false;
    } else {
      const missing = [];
      if (!cam.available) missing.push(cam.detail || "camera support unavailable");
      if (!ocr) missing.push("no OCR engine found");
      note.textContent = "Typing works fine — " + missing.join("; ") + ".";
      if (camBtn) {
        camBtn.disabled = true;
        camBtn.title = (cam.install_hint || "") + " " +
          ((caps.ocr_backends || []).map(b => b.install_hint).filter(Boolean)[0] || "");
      }
    }
  }

  // -------------------------------------------------------------- identify

  async function identify() {
    if (state.busy) return;
    const text = (e("scan-text").value || "").trim();
    if (!text) { toast("Type a card name or its corner text first.", "warn"); return; }
    await runIdentify(() => callApi("scan_identify", text, ""));
  }

  async function captureFromCamera() {
    if (state.busy) return;
    await runIdentify(() => callApi("scan_capture", 0));
  }

  async function runIdentify(fn) {
    state.busy = true;
    const host = e("scan-result");
    host.innerHTML = "<p class=\"panel-hint\">Looking…</p>";
    let result;
    try {
      result = await fn();
    } catch (err) {
      host.innerHTML = `<p class="panel-hint">${escape(err.message)}</p>`;
      state.busy = false;
      return;
    }
    state.lastResult = result;
    renderResult(result);
    state.busy = false;

    // Auto-add only on a certain match, and only if the user left the
    // checkbox on. Everything else waits for a human.
    const auto = e("scan-auto-add");
    if (result.auto_addable && auto && auto.checked && result.candidates.length) {
      await commit(result.candidates[0], defaultFinish(result.candidates[0]));
    }
  }

  function defaultFinish(candidate) {
    const finishes = candidate.finishes || [];
    return finishes.includes("nonfoil") ? "nonfoil" : (finishes[0] || "nonfoil");
  }

  function renderResult(result) {
    const host = e("scan-result");
    const badge = e("scan-confidence");
    const conf = result.confidence;

    const LABEL = {
      exact: ["Exact match", "conf-exact"],
      likely: ["Likely match", "conf-likely"],
      ambiguous: ["Needs a choice", "conf-ambiguous"],
      unknown: ["Not recognised", "conf-unknown"],
    };
    const [label, cls] = LABEL[conf] || LABEL.unknown;
    if (badge) {
      badge.textContent = label;
      badge.className = "status-text " + cls;
    }

    const idn = result.identity || {};
    const read = [];
    if (idn.name) read.push(`name “${idn.name}”`);
    if (idn.set_code) read.push(`set ${idn.set_code.toUpperCase()}`);
    if (idn.collector_number) read.push(`#${idn.collector_number}`);

    if (!result.candidates.length) {
      host.innerHTML =
        `<p class="panel-hint">Couldn't identify that.` +
        (read.length ? ` Read: ${escape(read.join(", "))}.` : "") +
        `</p><p class="panel-hint subtle">The set code and collector number in ` +
        `the card's bottom-left corner are the most reliable thing to enter.</p>`;
      return;
    }

    const heading = read.length
      ? `<p class="panel-hint subtle">Read: ${escape(read.join(", "))}</p>` : "";
    const askNote = result.auto_addable ? "" :
      `<p class="panel-hint">Pick the printing you're holding — prices differ ` +
      `enormously between them, so this isn't guessed for you.</p>`;

    host.innerHTML = heading + askNote + result.candidates.map((c, i) => {
      const finishBtns = (c.finishes || ["nonfoil"]).map(f => {
        const price = f === "foil" ? c.price_usd_foil : c.price_usd;
        return `<button class="btn btn-outline btn-slim" data-pick="${i}"
                        data-finish="${escape(f)}">
                  + ${escape(f)}${price === null || price === undefined
                                  ? "" : " · " + money(price)}
                </button>`;
      }).join(" ");
      const owned = c.owned ? `<span class="collection-tag collection-tag-owned">owned ${c.owned}</span>` : "";
      return `
        <div class="printing-row">
          <div class="printing-ident">
            <div class="collection-name">${escape(c.name)} ${owned}</div>
            <div class="subtle">${escape(c.set_name)} ·
              ${escape((c.set_code || "").toUpperCase())} #${escape(c.collector_number)}
              · ${escape(c.rarity || "")}</div>
          </div>
          <div class="printing-actions">${finishBtns}</div>
        </div>`;
    }).join("");

    host.onclick = async (ev) => {
      const btn = ev.target.closest("button[data-pick]");
      if (!btn) return;
      const candidate = result.candidates[Number(btn.dataset.pick)];
      await commit(candidate, btn.dataset.finish);
    };
  }

  // ----------------------------------------------------------- collections

  /**
   * Fill the "file into" list and the "also tag" boxes.
   *
   * Both are rebuilt from the same reply, and the filing target is excluded
   * from the tag boxes: the card is already in whatever it is filed into, so
   * offering it as an extra tag would be offering a no-op.
   *
   * Choices survive a refill. Scanning a box is a long job and a collection
   * created halfway through must not silently reset where the rest of the
   * box is going.
   */
  async function loadCollections() {
    const home = e("scan-collection");
    const tags = e("scan-tags");
    if (!home || !tags) return;

    const wasHome = home.value;
    const wasTagged = new Set(pickedTags());

    let list = [];
    try {
      const r = await callApi("list_collections");
      list = (r && r.collections) || [];
    } catch (err) {
      return;                  // scanning still works; it lands in the default
    }

    home.innerHTML = list.map(c =>
      `<option value="${escape(String(c.collection_id))}">${escape(c.name)}</option>`
    ).join("");
    if (wasHome && list.some(c => String(c.collection_id) === wasHome)) {
      home.value = wasHome;
    }

    const others = list.filter(c => String(c.collection_id) !== home.value);
    tags.innerHTML = others.length
      ? others.map(c => {
          const id = escape(String(c.collection_id));
          const on = wasTagged.has(String(c.collection_id)) ? " checked" : "";
          return `<label><input type="checkbox" data-tag="${id}"${on}>` +
                 `${escape(c.name)}</label>`;
        }).join("")
      : `<span class="subtle scan-tags-empty">No other lists yet — make one ` +
        `in the Collection tab.</span>`;
  }

  /** The collection ids ticked in the "also tag" boxes. */
  function pickedTags() {
    return Array.from(
      document.querySelectorAll("#scan-tags input[data-tag]:checked"),
      el => el.dataset.tag);
  }

  // ---------------------------------------------------------------- commit

  async function commit(candidate, finish) {
    const condition = e("scan-condition").value || "NM";
    const location = (e("scan-location").value || "").trim();
    const home = e("scan-collection");
    const homeId = home && home.value ? Number(home.value) : null;
    const tags = pickedTags().map(Number);
    let r;
    try {
      r = await callApi("scan_commit", candidate.printing_id, candidate.name,
                        finish, condition, location,
                        (state.lastResult && state.lastResult.confidence) || "manual",
                        homeId, tags);
    } catch (err) {
      toast("Add failed: " + err.message, "error");
      return;
    }
    // Says how many lists it went into, because a scanner that files
    // silently is one you have to go and check.
    const extra = (r && r.tagged_into && r.tagged_into.length)
      ? ` — tagged into ${r.tagged_into.length} more`
      : "";
    toast(`Added ${candidate.name} (${finish})${extra}`, "success");
    renderSession(r.session);
    // Clear for the next card — continuous scanning is the point.
    e("scan-text").value = "";
    e("scan-text").focus();
    if (typeof window.__builderInvalidateOwnership === "function") {
      try { window.__builderInvalidateOwnership(); } catch (err) { /* non-fatal */ }
    }
  }

  async function skip() {
    try {
      const r = await callApi("scan_skip", "unknown");
      renderSession(r.session);
    } catch (err) { /* non-fatal */ }
  }

  // --------------------------------------------------------------- session

  function renderSession(session) {
    if (!session) return;
    e("scan-count").textContent = session.scanned.toLocaleString();
    e("scan-added").textContent = session.added.toLocaleString();
    e("scan-value").textContent = money(session.value_usd);

    const note = e("scan-session-note");
    if (note) {
      const bits = [];
      if (session.needs_review) bits.push(`${session.needs_review} needed a choice`);
      if (session.skipped) bits.push(`${session.skipped} skipped`);
      // Unpriced cards are excluded from the value — say so rather than let
      // the total look complete.
      if (session.unpriced) {
        bits.push(`${session.unpriced} had no price and aren't in the total`);
      }
      note.textContent = bits.join(" · ");
    }

    const log = e("scan-log");
    if (log) {
      log.innerHTML = (session.entries || []).slice().reverse().slice(0, 25)
        .map(entry => {
          const set = entry.set_code
            ? `${entry.set_code.toUpperCase()} #${entry.collector_number}` : "";
          const price = entry.price_usd === null || entry.price_usd === undefined
            ? "" : money(entry.price_usd);
          const mark = entry.added ? "✓" : "·";
          return `<div class="scan-log-row ${entry.added ? "" : "scan-log-skipped"}">
                    <span class="scan-log-mark">${mark}</span>
                    <span class="scan-log-name">${escape(entry.card_name)}</span>
                    <span class="subtle">${escape(set)}</span>
                    <span class="scan-log-price">${price}</span>
                  </div>`;
        }).join("");
    }
  }

  async function refreshSession() {
    try {
      const r = await callApi("get_scan_session");
      renderSession(r.session);
    } catch (err) { /* non-fatal */ }
  }

  async function resetSession() {
    try {
      const r = await callApi("reset_scan_session");
      renderSession(r.session);
      const appraisal = e("scan-appraisal");
      if (appraisal) appraisal.innerHTML = "";
      toast("New session started — your cards are kept.", "info");
    } catch (err) {
      toast("Could not reset: " + err.message, "error");
    }
  }

  async function appraiseSession() {
    const host = e("scan-appraisal");
    if (!host) return;
    host.innerHTML = "<p class=\"panel-hint\">Estimating…</p>";
    let a;
    try {
      a = await callApi("appraise_scan_session", null);
    } catch (err) {
      host.innerHTML = `<p class="panel-hint">${escape(err.message)}</p>`;
      return;
    }
    if (!a.total_cards) {
      host.innerHTML = "<p class=\"panel-hint\">Nothing scanned yet.</p>";
      return;
    }
    const t = a.target_prices;
    host.innerHTML = `
      <div class="scan-appraisal">
        <div class="build-collection-row">
          <span class="build-collection-label">Market</span>
          <span class="build-collection-value">${money(a.market_value_usd)}</span>
        </div>
        <div class="build-collection-row">
          <span class="build-collection-label">Est. net proceeds</span>
          <span class="build-collection-value">${money(a.net_proceeds_usd)}</span>
        </div>
        <div class="build-collection-row">
          <span class="build-collection-label">Offer range</span>
          <span class="build-collection-value">${money(t.conservative_usd)} – ${money(t.aggressive_usd)}</span>
        </div>
        <p class="panel-hint subtle">Confidence: ${escape(a.confidence)} ·
          ${a.price_coverage_pct}% of cards priced</p>
        <ul class="scan-caveats">
          ${(a.caveats || []).map(c => `<li>${escape(c)}</li>`).join("")}
        </ul>
        <p class="panel-hint">This is an estimate, not a valuation or an offer.</p>
      </div>`;
  }

  // ---------------------------------------------------------------- wiring

  function wireOnce() {
    if (state.wired) return;
    if (!e("view-scan")) return;
    state.wired = true;

    const idBtn = e("scan-identify-btn");
    if (idBtn) idBtn.addEventListener("click", identify);

    const camBtn = e("scan-camera-btn");
    if (camBtn) camBtn.addEventListener("click", captureFromCamera);

    const text = e("scan-text");
    if (text) text.addEventListener("keydown", (ev) => {
      // Enter identifies; Shift+Enter still adds a newline, because corner
      // text is naturally two lines.
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        identify();
      }
    });

    const resetBtn = e("scan-reset-btn");
    if (resetBtn) resetBtn.addEventListener("click", resetSession);

    const appraiseBtn = e("scan-appraise-btn");
    if (appraiseBtn) appraiseBtn.addEventListener("click", appraiseSession);

    // Where it files changes what is worth tagging: the card is already in
    // whatever it is filed into, so that list must drop out of the boxes.
    const home = e("scan-collection");
    if (home) home.addEventListener("change", () => { void loadCollections(); });
  }

  async function activate() {
    wireOnce();
    await refreshCapabilities();
    await refreshSession();
    // Refilled every time the tab opens, so a list made halfway through a
    // box is available for the rest of it.
    await loadCollections();
    const text = e("scan-text");
    if (text) text.focus();
  }

  window.__scanActivate = activate;
  window.__scanState = state;
  window.__scanSkip = skip;
})();
