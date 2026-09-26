/* Densa Deck — card art wherever a card is named.

   The desktop has the room to show a card rather than just say its name,
   and most panels only ever had the name: analysis output, combo lines,
   suggestions, diffs. This is the one mechanism they all share.

   A panel opts in by marking up the name, nothing more:

     CardArt.ref(name)        <span data-card="Sol Ring">Sol Ring</span>
                              hover shows the full card beside it
     CardArt.thumb(name)      a small art crop for list rows, filled in
                              once the image is known; hover as above

   Anything with `data-card` that lands in the page is picked up by a
   MutationObserver, resolved in batches through `get_card_images`, and
   gets the hover preview by event delegation — so no panel has to call
   anything after rendering, and re-renders cost nothing extra.

   Images are Scryfall hotlinks, never files: see data/images.py. A name
   the catalogue does not know keeps its plain text and no thumbnail. */
(function () {
  "use strict";

  const cache = new Map();      // lower-case name -> entry | null (unknown)
  const waiting = new Set();    // names queued for the next batch
  let batchTimer = null;
  let inflight = Promise.resolve();

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ------------------------------------------------------------ resolving

  function lookup(name) {
    return cache.get(String(name || "").trim().toLowerCase());
  }

  function queue(names) {
    for (const raw of names) {
      const name = String(raw || "").trim();
      if (name && !cache.has(name.toLowerCase())) waiting.add(name);
    }
    if (waiting.size && !batchTimer) {
      // A render usually adds dozens of names in one go; one call for all.
      batchTimer = setTimeout(flush, 25);
    }
  }

  function flush() {
    batchTimer = null;
    const names = Array.from(waiting).slice(0, 400);
    names.forEach(n => waiting.delete(n));
    if (!names.length) return;
    inflight = inflight.then(async () => {
      let images = {};
      try {
        const r = await callApi("get_card_images", names);
        images = (r && r.images) || {};
      } catch (err) {
        // No bridge, no catalogue: leave the names as text. Do not cache
        // the miss, so a later render (after a card DB install) retries.
        return;
      }
      const byLower = {};
      for (const [k, v] of Object.entries(images)) byLower[k.toLowerCase()] = v;
      for (const n of names) cache.set(n.toLowerCase(), byLower[n.toLowerCase()] || null);
      paint(document);
    });
    if (waiting.size) batchTimer = setTimeout(flush, 0);
  }

  // ------------------------------------------------------------- painting

  function paint(root) {
    const scope = root && root.querySelectorAll ? root : document;
    const nodes = scope.querySelectorAll("[data-card-thumb]:not([data-card-painted])");
    for (const el of nodes) {
      const entry = lookup(el.getAttribute("data-card"));
      if (entry === undefined) continue;          // still resolving
      el.setAttribute("data-card-painted", "");
      if (!entry) { el.classList.add("card-thumb-none"); continue; }
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = el.classList.contains("card-thumb-full") ? entry.small : entry.art;
      img.onerror = () => { img.remove(); el.classList.add("card-thumb-none"); };
      el.appendChild(img);
    }
  }

  function scan(root) {
    const scope = root && root.querySelectorAll ? root : document;
    const names = [];
    if (scope.getAttribute && scope.hasAttribute("data-card")) {
      names.push(scope.getAttribute("data-card"));
    }
    for (const el of scope.querySelectorAll("[data-card]")) {
      names.push(el.getAttribute("data-card"));
    }
    queue(names);
    paint(scope.nodeType === 1 && scope.parentNode ? scope.parentNode : scope);
  }

  // -------------------------------------------------------------- preview

  let preview = null;
  let previewFor = null;
  let showTimer = null;

  function ensurePreview() {
    if (preview) return preview;
    preview = document.createElement("div");
    preview.className = "card-preview hidden";
    preview.setAttribute("aria-hidden", "true");
    preview.innerHTML = "<img alt=\"\">";
    document.body.appendChild(preview);
    return preview;
  }

  function place(anchor) {
    const box = ensurePreview();
    const r = anchor.getBoundingClientRect();
    const w = 244, h = 340, gap = 12;
    const vw = window.innerWidth, vh = window.innerHeight;
    // Beside the name, on whichever side has room; never over the cursor.
    let left = r.right + gap;
    if (left + w > vw - 8) left = r.left - gap - w;
    if (left < 8) left = Math.max(8, Math.min(vw - w - 8, r.left));
    let top = r.top + r.height / 2 - h / 2;
    top = Math.max(8, Math.min(vh - h - 8, top));
    box.style.left = left + "px";
    box.style.top = top + "px";
  }

  /** The same Scryfall image at another size ("small", "normal", "art_crop"...). */
  function sized(url, size) {
    return String(url || "").replace(
      /\/(small|normal|large|png|art_crop|border_crop)\/(front|back)\//,
      `/${size}/$2/`).replace(/\.png(\?|$)/, size === "png" ? ".png$1" : ".jpg$1");
  }

  function show(anchor) {
    // A row about a specific printing previews that printing.
    const own = anchor.getAttribute("data-card-img");
    const entry = own ? { normal: own } : lookup(anchor.getAttribute("data-card"));
    if (!entry || !entry.normal) return;
    const box = ensurePreview();
    const img = box.querySelector("img");
    if (img.getAttribute("src") !== entry.normal) img.src = entry.normal;
    place(anchor);
    box.classList.remove("hidden");
    previewFor = anchor;
  }

  function hide() {
    clearTimeout(showTimer);
    showTimer = null;
    previewFor = null;
    if (preview) preview.classList.add("hidden");
  }

  document.addEventListener("mouseover", (ev) => {
    const anchor = ev.target.closest && ev.target.closest("[data-card]");
    if (!anchor || anchor === previewFor) return;
    if (anchor.hasAttribute("data-card-nopreview")) return;
    clearTimeout(showTimer);
    // A short delay, so sweeping the mouse across a list does not strobe.
    showTimer = setTimeout(() => show(anchor), 90);
  });
  document.addEventListener("mouseout", (ev) => {
    const anchor = ev.target.closest && ev.target.closest("[data-card]");
    if (!anchor) return;
    if (ev.relatedTarget && anchor.contains(ev.relatedTarget)) return;
    hide();
  });
  window.addEventListener("scroll", hide, true);
  document.addEventListener("mousedown", hide, true);
  document.addEventListener("keydown", hide, true);

  // ----------------------------------------------------------- observing

  let pendingRoots = [];
  let scanTimer = null;
  const observer = new MutationObserver((records) => {
    for (const rec of records) {
      for (const node of rec.addedNodes) {
        if (node.nodeType === 1) pendingRoots.push(node);
      }
      if (rec.type === "attributes" && rec.target.nodeType === 1) {
        rec.target.removeAttribute("data-card-painted");
        pendingRoots.push(rec.target);
      }
    }
    if (pendingRoots.length && !scanTimer) {
      scanTimer = setTimeout(() => {
        scanTimer = null;
        const roots = pendingRoots;
        pendingRoots = [];
        for (const r of roots) if (r.isConnected) scan(r);
      }, 0);
    }
  });

  function start() {
    observer.observe(document.body, {
      childList: true, subtree: true,
      attributes: true, attributeFilter: ["data-card"],
    });
    scan(document);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  // ----------------------------------------------------------------- API

  window.CardArt = {
    /** Markup for a card name with the hover preview. */
    ref(name, label) {
      const n = String(name || "");
      return `<span class="card-ref" data-card="${esc(n)}">${esc(label == null ? n : label)}</span>`;
    },
    /**
     * Markup for a list-row thumbnail (art crop). `full` shows the whole card.
     *
     * `src` is a Scryfall image URL for a SPECIFIC printing — a collection
     * row, a scan candidate, a printing picker. Rows like those are about
     * which printing it is, so they must show that printing's art rather
     * than the catalogue default; hover shows the same printing.
     */
    thumb(name, { full = false, src = "" } = {}) {
      const cls = `card-thumb${full ? " card-thumb-full" : ""}`;
      if (src) {
        const shown = full ? sized(src, "small") : sized(src, "art_crop");
        return `<span class="${cls}" data-card="${esc(name)}" data-card-img="${esc(sized(src, "normal"))}">`
          + `<img src="${esc(shown)}" alt="" loading="lazy" onerror="this.parentNode.classList.add('card-thumb-none')"></span>`;
      }
      return `<span class="${cls}" data-card="${esc(name)}" data-card-thumb></span>`;
    },
    /** For code that builds DOM nodes rather than strings. */
    tag(el, name) {
      if (el && name) el.setAttribute("data-card", String(name));
      return el;
    },
    lookup,
    refresh: () => scan(document),
  };
})();
