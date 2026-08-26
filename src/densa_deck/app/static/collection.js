/* Densa Deck — Collection view (v0.7.0).
   Physical cards you own, tracked per printing / finish / condition.

   IIFE-wrapped like builder.js; consumes the globals app.js defines
   (callApi, toast, escape). Registers window.__collectionActivate so
   app.js's switchView can lazily initialise this view without needing to
   know anything about it.

   Design note: the collection works with or without the printing
   catalogue. Without it you still see every card, quantity, finish and
   condition — only set detail and prices go dark. So the setup gate is a
   prompt, never a wall. */
(function () {
  "use strict";

  const EMPTY_QUERY = {
    name_like: "", finish: "", condition: "", location: "",
    min_price: null, max_price: null, unpriced_only: false,
    sort: "name", limit: 60, offset: 0,
  };

  const state = {
    query: Object.assign({}, EMPTY_QUERY),
    items: [],
    total: 0,
    wired: false,
    ready: false,          // printing catalogue present
    dismissedSetup: false,
    pendingCard: null,     // card whose printings the modal is showing
  };

  function e(id) { return document.getElementById(id); }

  // The Build tab caches ownership per card name; tell it to forget after
  // any change here, or its badges silently drift out of date.
  function invalidateBuilderBadges() {
    if (typeof window.__builderInvalidateOwnership === "function") {
      try { window.__builderInvalidateOwnership(); } catch (err) { /* non-fatal */ }
    }
  }

  function money(v) {
    if (v === null || v === undefined) return "—";
    return "$" + Number(v).toFixed(2);
  }

  // ------------------------------------------------------------ status

  async function refreshStatus() {
    let status;
    try {
      status = await callApi("get_collection_status");
    } catch (err) {
      toast("Could not read collection: " + err.message, "error");
      return;
    }

    state.ready = !!(status.printings && status.printings.ready);
    const hasCards = status.collection.total_cards > 0;

    // Show the download prompt only when it would actually help: no
    // catalogue AND nothing tracked yet. A user who has cards but removed
    // the catalogue gets their collection, not a nag screen.
    const showSetup = !state.ready && !hasCards && !state.dismissedSetup;
    e("collection-setup").classList.toggle("hidden", !showSetup);
    e("collection-main").classList.toggle("hidden", showSetup);

    e("collection-total-cards").textContent = status.collection.total_cards.toLocaleString();
    e("collection-unique-cards").textContent = status.collection.unique_cards.toLocaleString();
    e("collection-unique-printings").textContent =
      status.collection.unique_printings.toLocaleString();

    renderPriceAge(status.printings);
    renderLocations(status.locations || []);
    refreshValue();
  }

  // Whole-collection valuation, kept separate from the listing so the page
  // total never gets mistaken for the collection total.
  async function refreshValue() {
    const valueEl = e("collection-value");
    const noteEl = e("collection-unpriced-note");
    if (!valueEl) return;
    let v;
    try {
      v = await callApi("get_collection_value", true);
    } catch (err) {
      valueEl.textContent = "—";
      return;
    }
    valueEl.textContent = money(v.total_value_usd);

    // Never present a total without saying how much of it is unknown — 8.6%
    // of printings carry no price at all, and on a big collection that is a
    // real hole in the number.
    if (noteEl) {
      noteEl.textContent = v.unpriced_copies
        ? `${v.unpriced_copies.toLocaleString()} card${v.unpriced_copies === 1 ? "" : "s"} ` +
          `have no price and are not included in this total.`
        : "";
    }
    renderDeltas(v.history);
  }

  function renderDeltas(history) {
    const host = e("collection-deltas");
    if (!host) return;
    if (!history || !history.available) {
      host.innerHTML = "";
      return;
    }
    const labels = { "1d": "24h", "7d": "7d", "30d": "30d" };
    const parts = [];
    for (const key of ["1d", "7d", "30d"]) {
      const d = history.deltas[key];
      // null means "we weren't tracking yet", which is not the same as
      // "nothing moved" — say nothing rather than imply zero.
      if (!d) continue;
      const up = d.delta_usd > 0;
      const flat = Math.abs(d.delta_usd) < 0.005;
      const cls = flat ? "delta-flat" : (up ? "delta-up" : "delta-down");
      const sign = flat ? "" : (up ? "+" : "−");
      parts.push(
        `<div class="collection-delta ${cls}">` +
        `<span class="delta-label">${labels[key]}</span>` +
        `<span class="delta-value">${sign}${money(Math.abs(d.delta_usd))}</span>` +
        `</div>`);
    }
    host.innerHTML = parts.join("");
  }

  function renderPriceAge(printings) {
    const el = e("collection-price-age");
    if (!el) return;
    if (!printings || !printings.ready) {
      el.innerHTML = "Printing data not installed — set details and prices are unavailable. " +
        "<a href=\"#\" id=\"collection-late-sync\">Download now</a>";
      const link = e("collection-late-sync");
      if (link) link.addEventListener("click", (ev) => { ev.preventDefault(); startSync(false); });
      return;
    }
    const hrs = printings.price_age_hours;
    const when = printings.synced_at ? printings.synced_at.replace("T", " ").replace("+00:00", " UTC") : "";
    if (printings.prices_stale) {
      // Scryfall refreshes prices once a day and calls them "dangerously
      // stale after 24 hours". Show the age rather than presenting an old
      // number as current — the user is about to make money decisions on it.
      const age = hrs === null || hrs === undefined ? "unknown age"
        : hrs > 48 ? Math.round(hrs / 24) + " days old" : Math.round(hrs) + " hours old";
      el.innerHTML = "Prices " + escape(age) + " (" + escape(when) + "). " +
        "<a href=\"#\" id=\"collection-refresh-prices\">Refresh</a>";
      const link = e("collection-refresh-prices");
      if (link) link.addEventListener("click", (ev) => { ev.preventDefault(); startSync(true); });
    } else {
      el.textContent = "Prices current as of " + when + ".";
    }
  }

  function renderLocations(locations) {
    const sel = e("collection-filter-location");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = "<option value=\"\">(anywhere)</option>" +
      locations.map(l => `<option value="${escape(l)}">${escape(l)}</option>`).join("");
    if (locations.indexOf(current) >= 0) sel.value = current;
  }

  // ------------------------------------------------------------- sync

  async function startSync(force) {
    try {
      const r = await callApi("printings_download_start", !!force);
      if (r && r.ok === false) { toast(r.error, "warn"); return; }
    } catch (err) {
      toast("Download failed to start: " + err.message, "error");
      return;
    }
    e("collection-progress-wrap").classList.remove("hidden");
    pollSync();
  }

  async function pollSync() {
    let p;
    try {
      p = await callApi("printings_download_progress");
    } catch (err) {
      toast("Lost track of the download: " + err.message, "error");
      return;
    }
    const fill = e("collection-progress-fill");
    const msg = e("collection-progress-msg");
    if (fill) fill.style.width = (p.pct || 0) + "%";
    if (msg) msg.textContent = p.message || "";

    if (!p.done) { setTimeout(pollSync, 400); return; }

    e("collection-progress-wrap").classList.add("hidden");
    if (p.error) {
      toast("Printing download failed: " + p.error, "error");
      return;
    }
    toast(p.message || "Printing data installed.", "success");
    await refreshStatus();
    await loadItems(false);
  }

  // ------------------------------------------------------------ items

  async function loadItems(append) {
    const q = Object.assign({}, state.query);
    let r;
    try {
      r = await callApi("list_collection", q);
    } catch (err) {
      toast("Could not list collection: " + err.message, "error");
      return;
    }
    state.total = r.total || 0;
    state.items = append ? state.items.concat(r.items || []) : (r.items || []);
    renderItems();

    const count = e("collection-count");
    if (count) {
      // Show the filtered subtotal only when a filter is actually narrowing
      // things — otherwise it duplicates the header and invites confusion
      // between "this page" and "the collection".
      const filtered = state.query.name_like || state.query.finish ||
        state.query.condition || state.query.location ||
        state.query.min_price !== null || state.query.max_price !== null ||
        state.query.unpriced_only;
      const base = state.total === 0 ? "Nothing here yet"
        : state.items.length + " / " + state.total;
      count.textContent = filtered && r.page_value_usd
        ? `${base} · ${money(r.page_value_usd)} shown`
        : base;
    }

    const more = e("collection-more-btn");
    if (more) more.classList.toggle("hidden", state.items.length >= state.total);
  }

  function renderItems() {
    const host = e("collection-list");
    if (!host) return;

    if (!state.items.length) {
      host.innerHTML = "<p class=\"panel-hint\">No cards match. Add one with " +
        "<strong>Find printings</strong> on the left.</p>";
      return;
    }

    host.innerHTML = state.items.map(it => {
      const setTxt = it.known_printing
        ? escape(it.set_code.toUpperCase()) + " #" + escape(it.collector_number)
        : "<span class=\"subtle\">unknown printing</span>";
      const finishTag = it.finish === "nonfoil" ? ""
        : `<span class="collection-tag collection-tag-${escape(it.finish)}">${escape(it.finish)}</span>`;
      const condTag = it.condition === "NM" ? ""
        : `<span class="collection-tag">${escape(it.condition)}</span>`;
      const loc = it.location
        ? `<span class="collection-tag collection-tag-loc">${escape(it.location)}</span>` : "";
      const val = it.stack_value_usd === null
        ? "<span class=\"subtle\">unpriced</span>" : money(it.stack_value_usd);
      return `
        <div class="collection-row" data-item="${it.item_id}">
          <div class="qty-controls">
            <button class="qty-btn" data-act="dec" data-item="${it.item_id}" title="Remove one">−</button>
            <span class="qty-value">${it.quantity}</span>
            <button class="qty-btn" data-act="inc" data-item="${it.item_id}" title="Add one">+</button>
          </div>
          <div class="collection-card">
            <div class="collection-name">${escape(it.card_name)} ${finishTag}${condTag}${loc}</div>
            <div class="collection-set subtle">${setTxt}</div>
          </div>
          <div class="collection-value">${val}</div>
          <button class="btn btn-outline btn-slim" data-act="card"
                  data-printing="${escape(it.printing_id || "")}"
                  data-name="${escape(it.card_name)}">View</button>
          <button class="btn btn-outline btn-slim" data-act="printings"
                  data-name="${escape(it.card_name)}">Printings</button>
        </div>`;
    }).join("");

    host.onclick = async (ev) => {
      const btn = ev.target.closest("button[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "printings") { openPrintings(btn.dataset.name); return; }
      if (act === "card") {
        openCard(btn.dataset.printing, btn.dataset.name);
        return;
      }

      const itemId = Number(btn.dataset.item);
      const item = state.items.find(i => i.item_id === itemId);
      if (!item) return;
      const next = item.quantity + (act === "inc" ? 1 : -1);
      try {
        await callApi("set_collection_item_quantity", itemId, next);
      } catch (err) {
        toast("Update failed: " + err.message, "error");
        return;
      }
      await refreshStatus();
      await loadItems(false);
      invalidateBuilderBadges();
    };
  }

  // -------------------------------------------------------- printings

  /**
   * One card: its art and what it does.
   *
   * The image is a Scryfall URL. Nothing is downloaded here and nothing is
   * served from this app — that is the licence position, and the browser's
   * own cache means a card looked at twice is only fetched once.
   */
  function cardImageUrl(printingId, size) {
    const id = String(printingId || "").trim().toLowerCase();
    if (id.length < 8 || !/^[0-9a-f][0-9a-f-]*$/.test(id)) return "";
    const ext = size === "png" ? "png" : "jpg";
    return `https://cards.scryfall.io/${size}/front/${id[0]}/${id[1]}/${id}.${ext}`;
  }

  function hideCard() {
    const modal = e("card-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  async function openCard(printingId, cardName) {
    const modal = e("card-modal");
    const body = e("card-modal-body");
    if (!modal || !body) return;
    e("card-modal-title").textContent = cardName || "Card";
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");

    const art = cardImageUrl(printingId, "normal");
    // The art is drawn immediately rather than after the lookup: it does not
    // depend on the catalogue, and waiting on the text to show the picture
    // would make a fast thing feel slow.
    const picture = art
      ? `<img class="card-art" src="${escape(art)}" alt="${escape(cardName || "")}" loading="lazy">`
      : `<p class="panel-hint">No art for this printing.</p>`;
    body.innerHTML = `<div class="card-detail-art">${picture}</div>
      <div class="card-detail-text"><p class="panel-hint">Loading…</p></div>`;

    let d;
    try {
      d = await callApi("get_card_detail", printingId || "", cardName || "");
    } catch (err) {
      body.querySelector(".card-detail-text").innerHTML =
        `<p class="panel-hint">Could not load the card text: ${escape(err.message)}</p>`;
      return;
    }

    if (d.unknown_card) {
      body.querySelector(".card-detail-text").innerHTML =
        "<p class=\"panel-hint\">This card is not in the local catalogue, so " +
        "there is no rules text to show. The art is right either way.</p>";
      return;
    }

    const faces = (d.faces || []).length > 1 ? d.faces : [];
    const stat = d.power || d.toughness
      ? `<p class="card-detail-stat">${escape(d.power || "")}/${escape(d.toughness || "")}</p>`
      : (d.loyalty ? `<p class="card-detail-stat">Loyalty ${escape(d.loyalty)}</p>` : "");
    const legal = Object.entries(d.legalities || {})
      .filter(([, v]) => v === "legal")
      .map(([fmt]) => escape(fmt))
      .join(", ");

    body.querySelector(".card-detail-text").innerHTML = `
      <p class="card-detail-type">${escape(d.type_line || "")}
        <span class="subtle">${escape((d.mana_cost || "").replace(/[{}]/g, " ").trim())}</span></p>
      ${d.oracle_text ? `<p class="card-detail-oracle">${escape(d.oracle_text)}</p>` : ""}
      ${stat}
      ${faces.map(f => `
        <div class="card-detail-face">
          <p class="card-detail-type"><strong>${escape(f.name)}</strong>
            <span class="subtle">${escape((f.mana_cost || "").replace(/[{}]/g, " ").trim())}</span></p>
          <p class="subtle">${escape(f.type_line || "")}</p>
          ${f.oracle_text ? `<p class="card-detail-oracle">${escape(f.oracle_text)}</p>` : ""}
        </div>`).join("")}
      <p class="subtle">${escape((d.set_code || "").toUpperCase())} ${escape(d.rarity || "")}</p>
      ${legal ? `<p class="subtle">Legal: ${legal}</p>` : ""}
      ${d.scryfall_url ? `<p><a href="${escape(d.scryfall_url)}" target="_blank" rel="noreferrer">Rulings and printings on Scryfall</a></p>` : ""}
      <p class="subtle card-detail-credit">Card images and data from Scryfall.
        Not affiliated with Wizards of the Coast.</p>`;
  }

  async function openPrintings(cardName) {
    if (!cardName) return;
    state.pendingCard = cardName;
    const modal = e("printings-modal");
    const list = e("printings-list");
    e("printings-modal-title").textContent = cardName;
    list.innerHTML = "<p class=\"panel-hint\">Loading…</p>";
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");

    let r;
    try {
      r = await callApi("get_card_printings", cardName);
    } catch (err) {
      list.innerHTML = `<p class="panel-hint">Could not load printings: ${escape(err.message)}</p>`;
      return;
    }

    if (!r.printings.length) {
      list.innerHTML = r.catalogue_ready
        ? `<p class="panel-hint">No printings found for “${escape(cardName)}”. Check the spelling.</p>`
        : "<p class=\"panel-hint\">Printing data isn't installed yet. " +
          "<a href=\"#\" id=\"printings-sync\">Download it</a> to pick exact printings.</p>";
      const link = e("printings-sync");
      if (link) link.addEventListener("click", (ev) => {
        ev.preventDefault(); hidePrintings(); startSync(false);
      });
      return;
    }

    list.innerHTML = r.printings.map(p => {
      // Only offer finishes this printing was actually made in — you cannot
      // own an etched copy of a card never printed etched.
      const finishBtns = p.finishes.map(f => {
        const price = f === "foil" ? p.price_usd_foil
          : f === "etched" ? p.price_usd_etched : p.price_usd;
        return `<button class="btn btn-outline btn-slim" data-add="${escape(p.printing_id)}"
                        data-finish="${escape(f)}" data-name="${escape(p.name)}"
                        data-oracle="${escape(p.oracle_id)}">
                  + ${escape(f)} ${price === null ? "" : "· " + money(price)}
                </button>`;
      }).join(" ");
      const ownedTag = p.owned
        ? `<span class="collection-tag collection-tag-owned">owned ${p.owned}</span>` : "";
      return `
        <div class="printing-row">
          <div class="printing-ident">
            <div class="collection-name">
              ${escape(p.set_name)} ${ownedTag}
            </div>
            <div class="subtle">${escape(p.set_code.toUpperCase())} #${escape(p.collector_number)}
              · ${escape(p.rarity)} · ${escape(p.released_at)}</div>
          </div>
          <div class="printing-actions">${finishBtns}</div>
        </div>`;
    }).join("");

    list.onclick = async (ev) => {
      const btn = ev.target.closest("button[data-add]");
      if (!btn) return;
      const condition = e("printings-condition").value || "NM";
      const location = (e("printings-location").value || "").trim();
      try {
        await callApi("add_to_collection", btn.dataset.add, btn.dataset.name, 1,
                      btn.dataset.finish, condition, "en", location, "",
                      btn.dataset.oracle || "");
      } catch (err) {
        toast("Add failed: " + err.message, "error");
        return;
      }
      toast(`Added ${btn.dataset.name} (${btn.dataset.finish})`, "success");
      await refreshStatus();
      await loadItems(false);
      invalidateBuilderBadges();
      openPrintings(state.pendingCard);   // refresh owned counts in place
    };
  }

  function hidePrintings() {
    const m = e("printings-modal");
    if (!m) return;
    m.classList.add("hidden");
    m.setAttribute("aria-hidden", "true");
  }

  // ------------------------------------------------------------ wiring

  let searchTimer = null;
  function debouncedReload() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query.offset = 0; loadItems(false); }, 180);
  }

  function wireOnce() {
    if (state.wired) return;
    state.wired = true;

    const sync = e("collection-sync-btn");
    const cardClose = e("card-close-btn");
    if (cardClose) cardClose.addEventListener("click", hideCard);
    const cardModal = e("card-modal");
    if (cardModal) cardModal.addEventListener("click", (ev) => {
      // Clicking the backdrop closes it; clicking the card does not.
      if (ev.target === cardModal) hideCard();
    });

    if (sync) sync.addEventListener("click", () => startSync(false));

    const skip = e("collection-skip-btn");
    if (skip) skip.addEventListener("click", () => {
      // Dismissal is per-session on purpose: it is a prompt, not a
      // permanent decision, and the collection is usable either way.
      state.dismissedSetup = true;
      refreshStatus();
    });

    const search = e("collection-search");
    if (search) search.addEventListener("input", () => {
      state.query.name_like = search.value;
      debouncedReload();
    });

    [["collection-filter-finish", "finish"],
     ["collection-filter-condition", "condition"],
     ["collection-filter-location", "location"]].forEach(([id, key]) => {
      const el = e(id);
      if (el) el.addEventListener("change", () => {
        state.query[key] = el.value;
        state.query.offset = 0;
        loadItems(false);
      });
    });

    // Price bounds. Blank means "no bound", never 0 — a cleared box must not
    // silently become a $0 floor.
    [["collection-min-price", "min_price"],
     ["collection-max-price", "max_price"]].forEach(([id, key]) => {
      const el = e(id);
      if (el) el.addEventListener("input", () => {
        state.query[key] = el.value === "" ? null : Number(el.value);
        debouncedReload();
      });
    });

    const unpriced = e("collection-unpriced-only");
    if (unpriced) unpriced.addEventListener("change", () => {
      state.query.unpriced_only = unpriced.checked;
      // A price range and "no price at all" are contradictory questions;
      // disable the range rather than silently ignoring it.
      ["collection-min-price", "collection-max-price"].forEach(id => {
        const el = e(id);
        if (el) el.disabled = unpriced.checked;
      });
      state.query.offset = 0;
      loadItems(false);
    });

    const sort = e("collection-sort");
    if (sort) sort.addEventListener("change", () => {
      state.query.sort = sort.value;
      state.query.offset = 0;
      loadItems(false);
    });

    const clear = e("collection-clear-filters");
    if (clear) clear.addEventListener("click", () => {
      state.query = Object.assign({}, EMPTY_QUERY);
      // Hand-sync the controls — state alone won't move the DOM.
      ["collection-search", "collection-filter-finish",
       "collection-filter-condition", "collection-filter-location",
       "collection-min-price", "collection-max-price"].forEach(id => {
        const el = e(id);
        if (el) { el.value = ""; el.disabled = false; }
      });
      const u = e("collection-unpriced-only");
      if (u) u.checked = false;
      const s = e("collection-sort");
      if (s) s.value = "name";
      loadItems(false);
    });

    const more = e("collection-more-btn");
    if (more) more.addEventListener("click", () => {
      state.query.offset = state.items.length;
      loadItems(true);
    });

    const addBtn = e("collection-add-btn");
    const addName = e("collection-add-name");
    if (addBtn) addBtn.addEventListener("click", () => openPrintings((addName.value || "").trim()));
    if (addName) addName.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") openPrintings((addName.value || "").trim());
    });

    ["printings-close-btn", "printings-done-btn"].forEach(id => {
      const el = e(id);
      if (el) el.addEventListener("click", hidePrintings);
    });
  }

  async function activate() {
    wireOnce();
    await refreshStatus();
    await loadItems(false);
  }

  window.__collectionActivate = activate;
  // Test/debug hook, mirroring builder.js's __builderState.
  window.__collectionState = state;
})();
