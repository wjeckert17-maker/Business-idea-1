// Service worker: the only place network requests are made.
//   * /api/scores  — this product's own API, batched by CRN (our data, our server).
//   * third-party  — opt-in only; one request at a time at human speed; results held in
//                    chrome.storage.session (memory only, cleared when the browser closes);
//                    never forwarded anywhere, never logged, never included in any analytics.
import { ADAPTERS, DEFAULT_ADAPTER } from "./adapters/thirdparty.js";

const DEFAULTS = { showOwnScores: true, thirdParty: false, adapter: DEFAULT_ADAPTER, apiBase: "http://localhost:3000", school: "Georgia Institute of Technology" };
const MIN_INTERVAL_MS = 2500;      // human browsing speed: one third-party request every 2.5 s at most
const MAX_QUEUE = 3;               // hovers beyond this are dropped, not deferred: no page-wide sweeps
const SESSION_TTL_MS = 30 * 60 * 1000;

async function settings() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

// ---- own scores -------------------------------------------------------------
async function ownScores(crns, apiBase) {
  const url = new URL("/api/scores", apiBase);
  for (const c of crns.slice(0, 100)) url.searchParams.append("crn", c);
  const res = await fetch(url, { credentials: "omit" });
  if (!res.ok) throw new Error(`api ${res.status}`);
  return res.json();
}

// ---- third-party: rate-limited queue + session cache --------------------------
let lastAt = 0;
let queue = [];
let running = false;

async function cached(key) {
  const got = await chrome.storage.session.get(key);
  const v = got[key];
  if (v && Date.now() - v.at < SESSION_TTL_MS) return v.value;
  return undefined;
}
async function remember(key, value) {
  await chrome.storage.session.set({ [key]: { at: Date.now(), value } });
}

function enqueue(job) {
  if (queue.length >= MAX_QUEUE) queue.shift().reject(new Error("dropped"));
  queue.push(job);
  if (!running) drain();
}
async function drain() {
  running = true;
  while (queue.length) {
    const job = queue.shift();
    if (job.signal?.aborted) { job.reject(new Error("aborted")); continue; }
    const wait = Math.max(0, lastAt + MIN_INTERVAL_MS - Date.now());
    if (wait) await new Promise(r => setTimeout(r, wait));
    lastAt = Date.now();
    try { job.resolve(await job.run()); } catch (e) { job.reject(e); }
  }
  running = false;
}

async function thirdParty(instructor, s) {
  const adapter = ADAPTERS[s.adapter];
  if (!adapter) return null;
  const granted = await chrome.permissions.contains({ origins: [adapter.origin] });
  if (!granted) return null;                       // opted in but permission missing: silently nothing
  const key = `tp:${s.adapter}:${instructor.toLowerCase()}`;
  const hit = await cached(key);
  if (hit !== undefined) return hit;
  const value = await new Promise((resolve, reject) => enqueue({ run: () => adapter.lookup(instructor, s.school), resolve, reject }));
  await remember(key, value ?? null);
  return value ?? null;
}

// ---- messages from the content script -----------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    const s = await settings();
    if (msg.type === "settings") return sendResponse(s);
    if (msg.type === "own-scores") {
      if (!s.showOwnScores) return sendResponse({ scores: {} });
      try { return sendResponse(await ownScores(msg.crns, s.apiBase)); } catch { return sendResponse({ scores: {} }); }
    }
    if (msg.type === "third-party") {
      if (!s.thirdParty || !msg.instructor) return sendResponse(null);
      try { return sendResponse(await thirdParty(msg.instructor, s)); } catch { return sendResponse(null); }
    }
    sendResponse(null);
  })();
  return true;   // keep the channel open for the async reply
});
