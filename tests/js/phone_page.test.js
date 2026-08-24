/**
 * Run the phone scan page in a real DOM and drive it through real events.
 *
 * The page is ~900 lines of browser JS that no Python test touches, and the
 * last two rounds of bugs — Enter swallowed in the textarea, a printing list
 * truncated at 25, a repeat guard that cleared on any missed frame — were all
 * reachable without a camera. Everything here goes through the page's own
 * code path: stub the network, click the buttons, read the DOM.
 */
const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const path = require("path");
const PAGE = path.join(__dirname, "..", "..", "src", "densa_deck",
                       "app", "static", "phone", "scan.html");

const failures = [];
let passed = 0;
function check(name, condition, detail) {
  if (condition) { passed++; return; }
  failures.push(detail ? `${name} — ${detail}` : name);
}

const html = fs.readFileSync(PAGE, "utf8");
const scriptErrors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => scriptErrors.push(String(e.message)));

// What the stubbed bridge will answer with, and what it was asked.
let nextIdentify = null;
const calls = [];

const mk = (n, set, num) => ({
  printing_id: set + "-" + num, name: n, set_code: set,
  set_name: set.toUpperCase(), collector_number: String(num),
  finishes: ["nonfoil", "foil"], price_usd: 1.5, price_usd_foil: 4.5,
});

// A session the page can render rows and +/- controls from.
const sessionState = {
  scanned: 2, added: 2, skipped: 0, needs_review: 0, value_usd: 6.0,
  unpriced: 0,
  entries: [{ card_name: "Fae Flight", added: false, confidence: "unknown" }],
  counts: [
    { card_name: "Sol Ring", printing_id: "som-79", set_code: "som",
      collector_number: "79", finish: "nonfoil", price_usd: 1.5, quantity: 2 },
    { card_name: "Arcane Signet", printing_id: "eld-331", set_code: "eld",
      collector_number: "331", finish: "foil", price_usd: 4.5, quantity: 1 },
  ],
};

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://127.0.0.1:8791/scan?t=test-token",
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(window) {
    // Installed before the page's own script runs, so every call it makes is
    // answered here rather than hitting a real bridge.
    window.fetch = async (url, opts) => {
      const route = String(url).replace("/api/", "");
      const body = JSON.parse((opts && opts.body) || "{}");
      calls.push({ route, body });
      let data = { ok: true };
      if (route === "identify") data = nextIdentify || { candidates: [] };
      if (route === "commit" || route === "adjust") {
        data = { ok: true, session: sessionState };
      }
      if (route === "session") data = { session: { scanned: 0, added: 0,
                                        value_usd: 0, needs_review: 0, entries: [] } };
      if (route === "capabilities") data = { ok: true, photo: true };
      if (route === "collections") data = {
        collections: [
          { collection_id: 1, name: "Main Collection", cards: 12, is_default: true },
          { collection_id: 2, name: "Trade box", cards: 4, is_default: false },
        ],
        master: { cards: 16, value_usd: 42 },
        default_collection_id: 1,
      };
      if (route === "new-collection") data = {
        collection: { collection_id: 3, name: body.name, cards: 0,
                      is_default: false },
      };
      return { status: 200, json: async () => data };
    };
    window.HTMLElement.prototype.scrollIntoView = () => {};
    window.navigator.vibrate = () => true;

    // jsdom decodes no video and paints no canvas, so the lens probe would
    // measure nothing and report every camera as dead. Give it a frame with
    // real structure in it so the probe path is genuinely exercised.
    Object.defineProperty(window.HTMLVideoElement.prototype, "videoWidth",
                          { get() { return 1920; } });
    Object.defineProperty(window.HTMLVideoElement.prototype, "videoHeight",
                          { get() { return 1080; } });
    window.HTMLCanvasElement.prototype.getContext = function () {
      const self = this;
      return {
        drawImage() {},
        getImageData(x, y, w, h) {
          const data = new Uint8ClampedArray(w * h * 4);
          for (let i = 0; i < w * h; i++) {
            // A chequer pattern: high local contrast, so sharpness is
            // non-zero and the "which lens is sharpest" logic has something
            // to compare.
            const px = ((i % w) + Math.floor(i / w)) % 2 ? 240 : 15;
            data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = px;
            data[i * 4 + 3] = 255;
          }
          return { data, width: w, height: h };
        },
        canvas: self,
      };
    };
    window.HTMLCanvasElement.prototype.toDataURL = () =>
      "data:image/jpeg;base64,TEST";

    // A phone's real camera stack: several back lenses, one front, and one
    // that enumerates but refuses to open — which is exactly the case that
    // filled the picker with dead entries.
    const fakeTrack = (deviceId) => ({
      getSettings: () => ({ deviceId }),
      getCapabilities: () => ({ focusMode: ["continuous"] }),
      applyConstraints: async () => {},
      stop: () => {},
    });
    window.navigator.mediaDevices = {
      enumerateDevices: async () => ([
        { kind: "videoinput", deviceId: "b0", label: "camera2 0, facing back" },
        { kind: "videoinput", deviceId: "f0", label: "camera2 1, facing front" },
        { kind: "videoinput", deviceId: "b1", label: "camera2 2, facing back" },
        { kind: "videoinput", deviceId: "b2", label: "camera2 3, facing back" },
        { kind: "audioinput", deviceId: "mic", label: "mic" },
      ]),
      getUserMedia: async (constraints) => {
        const id = (((constraints.video || {}).deviceId || {}).exact) || "b0";
        if (id === "b2") throw new Error("camera busy");
        return { getTracks: () => [fakeTrack(id)],
                 getVideoTracks: () => [fakeTrack(id)] };
      },
    };
    window.isSecureContext = true;
  },
});
const { window } = dom;
const doc = window.document;
const $ = (id) => doc.getElementById(id);
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function identifyWith(candidates, extra) {
  nextIdentify = Object.assign(
    { confidence: "ambiguous", auto_addable: false, candidates }, extra || {});
  if ($("type-panel").classList.contains("hidden")) click($("type-btn"));
  $("text").value = "whatever";
  click($("id-btn"));
  await sleep(60);
}

(async () => {
  await sleep(200);
  check("page loads without script errors", scriptErrors.length === 0,
        scriptErrors.join(" | "));

  // --- the running screen starts short -----------------------------------
  check("typing panel starts collapsed", $("type-panel").classList.contains("hidden"));
  check("add-as panel starts collapsed", $("addas-panel").classList.contains("hidden"));
  check("camera settings start collapsed", $("cam-settings").classList.contains("hidden"));
  check("camera settings button hidden until the camera starts",
        $("cam-settings-btn").classList.contains("hidden"));

  // --- panels open and close ---------------------------------------------
  click($("type-btn"));
  check("typing panel opens", !$("type-panel").classList.contains("hidden"));
  check("typing button relabels", /Hide/.test($("type-btn").textContent));
  click($("type-btn"));
  check("typing panel closes again", $("type-panel").classList.contains("hidden"));

  click($("addas-btn"));
  check("add-as panel opens", !$("addas-panel").classList.contains("hidden"));
  check("add-as summary survives its relabel", !!$("addas-summary"));
  $("condition").value = "LP";
  $("condition").dispatchEvent(new window.Event("change"));
  check("add-as summary tracks the condition",
        /Lightly Played/.test($("addas-btn").textContent),
        $("addas-btn").textContent);

  // --- Enter must make a new line, not submit -----------------------------
  const before = calls.length;
  const ev = new window.KeyboardEvent("keydown",
    { key: "Enter", cancelable: true, bubbles: true });
  $("text").dispatchEvent(ev);
  await sleep(40);
  check("Enter is not swallowed in the card textarea", !ev.defaultPrevented);
  check("Enter does not submit", calls.length === before);

  // --- a short list: rows with per-finish buttons --------------------------
  await identifyWith([mk("Fae Flight", "mkm", 56), mk("Fae Flight", "mkm", 295)]);
  let host = $("result");
  check("short list renders a row per printing",
        host.querySelectorAll(".pick").length >= 2);
  check("short list offers a way out", !!host.querySelector("button[data-none]"));
  const icon = host.querySelector("img.seticon");
  check("set symbol is shown", !!icon);
  check("symbol is hotlinked from Scryfall",
        icon && icon.src === "https://svgs.scryfall.io/sets/mkm.svg",
        icon && icon.src);

  // --- the Arcane Signet case: 88 printings --------------------------------
  const many = Array.from({ length: 88 },
    (_, i) => mk("Arcane Signet", "s" + i, i + 1));
  await identifyWith(many);
  host = $("result");
  const tiles = host.querySelectorAll(".iconpick button[data-i]");
  check("long list shows EVERY printing", tiles.length === 88,
        `showed ${tiles.length} of 88`);
  check("long list still offers a way out",
        !!host.querySelector("button[data-none]"));

  // Picking a symbol asks for the finish rather than filing blind.
  click(tiles[42]);
  await sleep(40);
  host = $("result");
  check("tapping a symbol asks for the finish",
        host.querySelectorAll("button[data-f]").length === 2,
        String(host.querySelectorAll("button[data-f]").length));
  check("finish step can go back", !!host.querySelector("button[data-back]"));

  // --- committing shows the full-screen confirmation ----------------------
  click(host.querySelector("button[data-f]"));
  await sleep(80);
  const commit = calls.filter(c => c.route === "commit").pop();
  check("commit sends the printing that was tapped",
        commit && commit.body.printing_id === "s42-43",
        commit && commit.body.printing_id);
  check("commit carries the chosen condition",
        commit && commit.body.condition === "LP", commit && commit.body.condition);
  check("confirmation covers the screen", $("flash").classList.contains("show"));
  check("confirmation names the card", /Arcane Signet/.test($("flash-name").textContent));
  check("confirmation shows the printing",
        /#43/.test($("flash-meta").textContent), $("flash-meta").textContent);

  // --- "none of these" files nothing --------------------------------------
  await identifyWith([mk("Fae Flight", "mkm", 56)]);
  const commitsBefore = calls.filter(c => c.route === "commit").length;
  click($("result").querySelector("button[data-none]"));
  await sleep(40);
  check("'none of these' files nothing",
        calls.filter(c => c.route === "commit").length === commitsBefore);
  check("'none of these' explains what to do next",
        /corner/i.test($("result").textContent), $("result").textContent.slice(0, 60));

  // --- an auto-addable result still confirms visibly ----------------------
  await identifyWith([mk("Black Lotus", "lea", 233)],
                     { confidence: "exact", auto_addable: true });
  check("an exact match is filed automatically",
        calls.filter(c => c.route === "commit").length === commitsBefore + 1);
  check("an automatic add still shows the confirmation",
        /Black Lotus/.test($("flash-name").textContent));


  // --- per-card +/- --------------------------------------------------------
  // Boxes hold playsets and hands slip, so each filed card needs to be
  // adjustable in place rather than only on the desktop afterwards.
  await identifyWith([mk("Sol Ring", "som", 79)],
                     { auto_addable: true, confidence: "exact" });
  const log = $("log");
  check("session lists one row per printing, not one per scan",
        log.querySelectorAll(".item .qty").length === 2,
        String(log.querySelectorAll(".item .qty").length));
  check("each row shows its count",
        /2/.test(log.querySelector(".item .qty b").textContent));
  check("foil copies are marked", /foil/i.test(log.textContent));
  check("cards that were not added still appear",
        /Fae Flight/.test(log.textContent));

  const plus = log.querySelector("button[data-adj='1']");
  const minus = log.querySelector("button[data-adj='-1']");
  check("every row offers +", !!plus);
  check("every row offers -", !!minus);

  click(plus);
  await sleep(80);
  let adj = calls.filter(c => c.route === "adjust").pop();
  check("+ asks for one more copy", adj && adj.body.delta === 1,
        adj && String(adj.body.delta));
  check("+ names the printing", adj && adj.body.printing_id === "som-79",
        adj && adj.body.printing_id);
  check("+ carries the finish", adj && adj.body.finish === "nonfoil");

  click(log.querySelector("button[data-adj='-1']"));
  await sleep(80);
  adj = calls.filter(c => c.route === "adjust").pop();
  check("- takes one back", adj && adj.body.delta === -1,
        adj && String(adj.body.delta));

  // --- the lens picker -----------------------------------------------------
  // A dropdown of "camera2 0, facing back" is unusable, and the labels lie:
  // filtering on them hid one of three back cameras and offered four "front"
  // ones on a phone with a single front camera. So every camera is listed and
  // facing is measured by opening it.
  click($("cam-btn"));
  await sleep(400);
  check("camera settings appear once the camera is on",
        !$("cam-settings-btn").classList.contains("hidden"));
  click($("cam-settings-btn"));
  await sleep(60);
  const grid = $("lens-grid");
  check("lens picker is shown when there is a choice",
        !$("lens-row").classList.contains("hidden"));
  check("every camera is listed, none hidden by a label guess",
        grid.querySelectorAll("button[data-dev]").length === 4,
        String(grid.querySelectorAll("button[data-dev]").length));
  check("lenses start untested", /untested/.test(grid.textContent));
  check("the live camera is reported back",
        /Using/.test($("lens-status").textContent),
        $("lens-status").textContent);

  // Probing marks the lens that cannot open instead of leaving it selectable.
  click($("lens-test-btn"));
  await sleep(16000);
  check("a lens that will not open is marked unavailable",
        /unavailable/.test(grid.textContent), grid.textContent.slice(0, 140));
  check("an unopenable lens cannot be chosen",
        grid.querySelector("button[data-dev='b2']").disabled === true);
  check("a working lens reports a focus score",
        /focus \d/.test(grid.textContent), grid.textContent.slice(0, 140));

  // Switching lenses must visibly take effect.
  click(grid.querySelector("button[data-dev='b1']"));
  await sleep(300);
  check("tapping a lens switches to it",
        /b1/.test(window.localStorage.getItem("densa-deck-lens") || ""),
        window.localStorage.getItem("densa-deck-lens"));


  // --- collections ---------------------------------------------------------
  // Every card scanned joins the master collection; the picker only chooses
  // which grouping inside it, so this must never be able to block a scan.
  const picker = $("collection-select");
  check("the collection picker is populated",
        picker.options.length === 2, String(picker.options.length));
  check("collections show their counts", /Trade box \(4\)/.test(picker.textContent),
        picker.textContent);
  check("the default is marked", /default/.test(picker.textContent));
  check("the default is selected first", picker.value === "1", picker.value);

  // Filing a card carries the chosen collection.
  picker.value = "2";
  picker.dispatchEvent(new window.Event("change"));
  await identifyWith([mk("Black Lotus", "lea", 233)],
                     { confidence: "exact", auto_addable: true });
  const filed = calls.filter(c => c.route === "commit").pop();
  check("a scan is filed into the chosen collection",
        filed && filed.body.collection_id === 2,
        filed && String(filed.body.collection_id));

  // A new collection can be made mid-run, without leaving the page.
  click($("collection-new-btn"));
  check("the new-collection field opens",
        !$("collection-new").classList.contains("hidden"));
  $("collection-name").value = "Bulk";
  click($("collection-create-btn"));
  await sleep(80);
  const made = calls.filter(c => c.route === "new-collection").pop();
  check("creating a collection sends the name", made && made.body.name === "Bulk",
        made && made.body.name);
  check("the field closes once created",
        $("collection-new").classList.contains("hidden"));

  // Renaming and deleting are deliberately absent from the phone: both can
  // move or destroy cards and belong where the whole collection is visible.
  check("the phone cannot delete a collection",
        !/delete-collection/.test(doc.body.innerHTML));
  console.log(`\n  ${passed} checks passed`);
  if (failures.length) {
    console.log(`  ${failures.length} FAILED:`);
    failures.forEach(f => console.log("    x " + f));
    process.exit(1);
  }
  console.log("  all phone-page checks passed");
  process.exit(0);
})();
