// Content script for Banner 9 class-search results. Finds section rows, asks the background worker
// for this product's scores by CRN (one batched request per page render), and injects a badge.
// Third-party enrichment is requested only on hover/click of a badge, one section at a time.
(() => {
  const SEEN = new WeakSet();
  const SCHOOL_CELL = 'td[data-property="courseReferenceNumber"]';

  const fmt = (x, d = 1) => (x == null ? "—" : Number(x).toFixed(d));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  function instructorOf(row) {
    const cell = row.querySelector('td[data-property="instructor"]');
    if (!cell) return null;
    const a = cell.querySelector("a.email");                       // Banner renders "Family, Given (Primary)"
    const raw = (a?.textContent || cell.textContent || "").replace(/\(Primary\)/i, "").trim();
    if (!raw) return null;
    const [family, given] = raw.split(",").map(x => x.trim());
    return given ? `${given} ${family}` : family;                  // "Kristine Nagel", the form a person would type
  }

  function badge(crn, data) {
    const b = el("div", "pl-badge");
    b.dataset.crn = crn;
    if (!data) { b.classList.add("pl-badge--none"); b.textContent = "no score"; return b; }
    const q = el("span", "pl-q", `quality ${data.quality >= 0 ? "+" : ""}${fmt(data.quality, 2)}`);
    const d = el("span", "pl-d", `difficulty ${fmt(data.difficulty, 0)}th pct`);
    const c = el("span", "pl-c", `${Math.round(data.confidence * 100)}% from data`);
    b.append(q, d, c);
    if (data.history?.grade_n) {
      b.append(el("span", "pl-h", `${fmt(data.history.mean_gpa, 2)} avg GPA (n=${data.history.grade_n})`));
    }
    if (data.low_confidence_sources?.length) b.append(el("span", "pl-low", "thin data"));
    const tp = el("span", "pl-tp");            // third-party slot: empty until hover, stays empty on failure
    b.append(tp);
    return b;
  }

  async function enrich(b, instructor) {
    if (b.dataset.tpState) return;             // fetched, pending or failed: never retry on every hover
    b.dataset.tpState = "pending";
    const slot = b.querySelector(".pl-tp");
    try {
      const r = await chrome.runtime.sendMessage({ type: "third-party", instructor });
      if (r && typeof r.rating === "number") {
        slot.textContent = `${fmt(r.rating)}/5 · diff ${fmt(r.difficulty)} · ${r.would_take_again == null ? "" : Math.round(r.would_take_again * 100) + "% again · "}n=${r.n}`;
        slot.title = `Fetched in your browser from ${r.source_url}; not stored or sent anywhere.`;
        b.dataset.tpState = "done";
      } else {
        b.dataset.tpState = "none";            // nothing to show, and nothing to say about it
      }
    } catch { b.dataset.tpState = "none"; }
  }

  async function process(rows) {
    const fresh = rows.filter(r => !SEEN.has(r));
    for (const r of fresh) SEEN.add(r);
    const crns = fresh.map(r => r.querySelector(SCHOOL_CELL)?.textContent.trim()).filter(Boolean);
    if (!crns.length) return;
    let scores = {};
    try { const resp = await chrome.runtime.sendMessage({ type: "own-scores", crns }); scores = resp?.scores ?? {}; } catch { scores = {}; }
    const s = await chrome.runtime.sendMessage({ type: "settings" }).catch(() => ({ thirdParty: false }));
    for (const row of fresh) {
      const cell = row.querySelector(SCHOOL_CELL);
      if (!cell) continue;
      const crn = cell.textContent.trim();
      const b = badge(crn, scores[crn]);
      cell.appendChild(b);
      const instructor = instructorOf(row);
      if (s?.thirdParty && instructor && scores[crn]) {
        let timer = null;
        b.addEventListener("mouseenter", () => { timer = setTimeout(() => enrich(b, instructor), 400); });   // dwell, not fly-over
        b.addEventListener("mouseleave", () => clearTimeout(timer));
        b.addEventListener("click", () => enrich(b, instructor));
      }
    }
  }

  const scan = () => process(Array.from(document.querySelectorAll("table tbody tr")).filter(r => r.querySelector(SCHOOL_CELL)));
  new MutationObserver(() => scan()).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
