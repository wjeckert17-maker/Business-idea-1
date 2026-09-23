import("./adapters/thirdparty.js").then(async ({ ADAPTERS, DEFAULT_ADAPTER }) => {
  const DEFAULTS = { showOwnScores: true, thirdParty: false, adapter: DEFAULT_ADAPTER, apiBase: "http://localhost:3000" };
  const s = { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  const own = document.getElementById("own"), tp = document.getElementById("tp"), api = document.getElementById("api");
  document.getElementById("tpName").textContent = ADAPTERS[s.adapter].label;
  own.checked = s.showOwnScores; tp.checked = s.thirdParty; api.value = s.apiBase;
  own.onchange = () => chrome.storage.sync.set({ showOwnScores: own.checked });
  api.onchange = () => chrome.storage.sync.set({ apiBase: api.value.trim() });
  tp.onchange = async () => {
    const origin = ADAPTERS[s.adapter].origin;
    if (tp.checked) {
      // The third-party host permission is requested only here, at the moment of opting in.
      const ok = await chrome.permissions.request({ origins: [origin] });
      if (!ok) { tp.checked = false; return; }
      await chrome.storage.sync.set({ thirdParty: true });
    } else {
      await chrome.storage.sync.set({ thirdParty: false });
      await chrome.storage.session.clear();                         // drop anything cached this session
      await chrome.permissions.remove({ origins: [origin] }).catch(() => {});
    }
  };
});
