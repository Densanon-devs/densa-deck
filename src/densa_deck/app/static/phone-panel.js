/* Densa Deck — Settings panel for phone scanning over Tailscale.

   Reports what is actually true about this machine rather than assuming:
   whether Tailscale is installed and running, whether a phone is on the
   tailnet right now, and whether `tailscale serve` is publishing HTTPS. The
   last one decides whether the phone gets a live camera or falls back to the
   OS camera app, so it's surfaced rather than left to fail silently. */
(function () {
  "use strict";

  const state = { wired: false, status: null };
  function e(id) { return document.getElementById(id); }

  async function refresh() {
    if (!e("phone-status-card")) return;
    let s;
    try {
      s = await callApi("get_phone_status");
    } catch (err) {
      e("phone-status-card").textContent = "Could not read status: " + err.message;
      return;
    }
    state.status = s;
    renderStatus(s);
    renderShare(s);
    renderScanInstall();
  }

  // Photo scanning needs OpenCV on this machine. Offer it as a button rather
  // than printing a pip command at someone.
  async function renderScanInstall() {
    const row = e("scan-install-row");
    if (!row) return;
    let caps;
    try {
      caps = await callApi("get_scan_capabilities");
    } catch (err) {
      row.classList.add("hidden");
      return;
    }
    if (caps.photo_ready) { row.classList.add("hidden"); return; }

    const note = e("scan-install-note");
    const btn = e("scan-install-btn");
    if (caps.can_auto_install) {
      row.classList.remove("hidden");
      note.textContent =
        `Photo scanning ("Take a photo" on your phone) needs a one-time `
        + `~${caps.install_size_mb} MB download. Typing a card works without it.`;
      btn.classList.remove("hidden");
    } else if (!caps.camera.available) {
      // Frozen build: no pip, so a button here would be a lie.
      row.classList.remove("hidden");
      note.textContent = "Photo scanning isn't available in this build. "
        + "Typing a card's name or corner text works.";
      btn.classList.add("hidden");
    } else {
      row.classList.add("hidden");
    }
  }

  async function installScanSupport() {
    const btn = e("scan-install-btn");
    const wrap = e("scan-install-progress");
    btn.disabled = true;
    btn.textContent = "Installing...";
    wrap.classList.remove("hidden");
    try {
      const r = await callApi("install_scan_support_start");
      if (r && r.ok === false) { toast(r.error, "error"); btn.disabled = false; return; }
    } catch (err) {
      toast("Could not start: " + err.message, "error");
      btn.disabled = false;
      return;
    }
    const tick = async () => {
      let p;
      try { p = await callApi("install_scan_support_progress"); }
      catch (err) { return; }
      e("scan-install-progress-fill").style.width = (p.pct || 0) + "%";
      e("scan-install-progress-msg").textContent = p.message || "";
      if (!p.done) { setTimeout(tick, 800); return; }
      btn.disabled = false;
      btn.textContent = "Enable photo scanning";
      if (p.error) {
        toast(p.message || "Install failed", "error");
        return;
      }
      toast("Photo scanning is ready.", "success");
      setTimeout(() => { wrap.classList.add("hidden"); refresh(); }, 1800);
    };
    tick();
  }

  function renderStatus(s) {
    const ts = s.tailscale || {};
    const card = e("phone-status-card");
    const rows = [];

    if (!ts.installed) {
      card.className = "card missing";
      card.innerHTML =
        "<strong>Tailscale isn't installed.</strong>" +
        "<p class=\"panel-hint\">It's what lets your phone reach this machine " +
        "securely from anywhere, without opening anything to the internet. " +
        "<a href=\"#\" id=\"phone-ts-link\">tailscale.com/download</a></p>";
      const link = e("phone-ts-link");
      if (link) link.addEventListener("click", (ev) => {
        ev.preventDefault();
        callApi("open_external", "https://tailscale.com/download").catch(() => {});
      });
      return;
    }

    if (!ts.running) {
      card.className = "card warning";
      card.innerHTML = "<strong>Tailscale is installed but not connected.</strong>" +
        "<p class=\"panel-hint\">Sign in to Tailscale, then come back. " +
        (ts.backend_state ? "State: " + escape(ts.backend_state) : "") + "</p>";
      return;
    }

    rows.push("<strong>This machine:</strong> " + escape(ts.dns_name || "(unknown)"));

    const phones = ts.phones_online || [];
    if (phones.length) {
      rows.push("<strong>Phone online:</strong> " +
                phones.map(p => escape(p.name)).join(", "));
    } else {
      rows.push("<span class=\"subtle\">No phone currently on your tailnet — " +
                "open the Tailscale app on your phone.</span>");
    }

    const serve = s.serve || {};
    const reachable = s.bridge && s.bridge.reachable_from_phone;
    if (!reachable && s.bridge && s.bridge.running) {
      // No tailnet address bound means the link would hang, not fail.
      rows.push("<span style=\"color:var(--color-warning,#ecc94b)\">" +
                "No tailnet address on this machine, so your phone can't " +
                "reach it. Is Tailscale connected?</span>");
      card.className = "card warning";
    } else if (serve.configured) {
      rows.push("<span style=\"color:var(--color-accent-green,#48bb78)\">" +
                "Ready over HTTPS — the live camera will work too.</span>");
      card.className = "card ready";
    } else {
      rows.push("<span style=\"color:var(--color-accent-green,#48bb78)\">" +
                "Ready — your phone connects straight over Tailscale.</span>");
      rows.push("<span class=\"subtle\">Type a card, or use your phone's " +
                "normal camera app. A live viewfinder would need HTTPS, " +
                "which isn't set up (and doesn't need to be).</span>");
      card.className = "card ready";
    }
    card.innerHTML = rows.map(r => "<div>" + r + "</div>").join("");
  }

  function renderShare(s) {
    const running = s.bridge && s.bridge.running;
    e("phone-start-btn").classList.toggle("hidden", !!running);
    e("phone-stop-btn").classList.toggle("hidden", !running);
    const unpairBtn = e("phone-unpair-btn");
    if (unpairBtn) unpairBtn.classList.toggle("hidden", !running);
    e("phone-status-text").textContent = running
      ? "Sharing — your phone stays paired across restarts"
      : "Not sharing";
    e("phone-share-block").classList.toggle("hidden", !running);
    if (!running) return;

    const url = s.phone_url || "";
    e("phone-url").value = url || "(Tailscale name unavailable)";
    renderQr(s.qr, url);

    const serve = s.serve || {};
    const hint = e("phone-serve-hint");
    // Only offer the HTTPS route when the phone path already works and the
    // user might want a live viewfinder on top. Never as a required step.
    const reachable2 = s.bridge && s.bridge.reachable_from_phone;
    hint.classList.toggle("hidden", !!serve.configured || !reachable2);
    if (serve.configured || !reachable2) return;

    // Three states, and telling someone to run `tailscale serve` in the
    // wrong one hangs their terminal with no error.
    const g = s.https || {};
    const body = e("phone-serve-body");
    const cmdWrap = e("phone-serve-cmd-wrap");
    if (!body) return;

    if (g.state === "https_not_enabled") {
      body.innerHTML =
        "<p class=\"panel-hint\"><strong>" + escape(g.headline || "") + "</strong></p>" +
        "<p class=\"panel-hint\">" + escape(g.detail || "") + "</p>" +
        "<p class=\"panel-hint subtle\">" + escape(g.cost || "") + "</p>" +
        "<button id=\"phone-https-admin\" class=\"btn btn-outline btn-slim\">" +
        "Open Tailscale DNS settings</button>";
      if (cmdWrap) cmdWrap.classList.add("hidden");
      const btn = e("phone-https-admin");
      if (btn) btn.addEventListener("click", () => {
        callApi("open_external", g.admin_url).catch(() => {});
      });
      return;
    }

    body.innerHTML =
      "<p class=\"panel-hint\">" + escape(g.headline || "Publish over HTTPS") + " — " +
      escape(g.detail || "") + "</p>";
    if (cmdWrap) cmdWrap.classList.remove("hidden");
    const cmd = e("phone-serve-cmd");
    if (cmd) cmd.value = g.command || s.serve_command || "";
  }

  function renderQr(matrix, url) {
    const host = e("phone-qr");
    if (!host) return;
    if (!matrix || !matrix.length) {
      // qrcode is optional; the link alone is still enough to pair.
      host.innerHTML = "<p class=\"panel-hint subtle\">Open the link on your " +
        "phone to pair.</p>";
      return;
    }
    // Rendered as one SVG path rather than a grid of elements — a 45x45
    // matrix would otherwise be 2,000 DOM nodes for a static image.
    const n = matrix.length;
    let d = "";
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if (matrix[y][x]) d += `M${x} ${y}h1v1h-1z`;
      }
    }
    host.innerHTML =
      `<svg viewBox="0 0 ${n} ${n}" width="180" height="180" role="img"
            aria-label="Pairing QR code" shape-rendering="crispEdges">
         <rect width="${n}" height="${n}" fill="#ffffff"/>
         <path d="${d}" fill="#000000"/>
       </svg>`;
  }

  function wireOnce() {
    if (state.wired) return;
    if (!e("phone-start-btn")) return;
    state.wired = true;

    e("phone-start-btn").addEventListener("click", async () => {
      try {
        const r = await callApi("phone_bridge_start");
        if (r && r.ok === false) { toast(r.error, "error"); return; }
      } catch (err) {
        toast("Could not start sharing: " + err.message, "error");
        return;
      }
      toast("Sharing started — scan the QR with your phone.", "success");
      await refresh();
    });

    e("phone-stop-btn").addEventListener("click", async () => {
      try {
        await callApi("phone_bridge_stop");
      } catch (err) { /* stopping should never block */ }
      toast("Sharing stopped. Your phone stays paired for next time.", "info");
      await refresh();
    });

    const unpair = e("phone-unpair-btn");
    if (unpair) unpair.addEventListener("click", async () => {
      // Irreversible from the phone's side: whoever holds that link loses it
      // and has to scan a new QR, which may be nowhere near them.
      if (!confirm("Unpair your phone?\n\nThe link saved on it will stop " +
                   "working and you'll need to scan a new QR code from this " +
                   "computer to pair again.")) return;
      try {
        await callApi("phone_unpair");
      } catch (err) {
        toast("Could not unpair: " + err.message, "error");
        return;
      }
      toast("Phone unpaired. The old link no longer works.", "info");
      await refresh();
    });

    const installBtn = e("scan-install-btn");
    if (installBtn) installBtn.addEventListener("click", installScanSupport);

    const copy = e("phone-copy-btn");
    if (copy) copy.addEventListener("click", () => {
      const url = e("phone-url").value || "";
      navigator.clipboard.writeText(url).then(
        () => { e("phone-copy-status").textContent = "Copied"; },
        () => { e("phone-copy-status").textContent = "Copy failed"; });
    });

    const copyCmd = e("phone-copy-cmd-btn");
    if (copyCmd) copyCmd.addEventListener("click", () => {
      navigator.clipboard.writeText(e("phone-serve-cmd").value || "").then(
        () => toast("Command copied", "success"),
        () => toast("Copy failed", "error"));
    });
  }

  window.__phonePanelRefresh = async function () {
    wireOnce();
    await refresh();
  };
})();
