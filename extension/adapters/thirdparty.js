// The ONE place third-party rating sites are described. Adding a site = adding an entry here plus its
// origin under optional_host_permissions. Every adapter must be render-only: it returns numbers for
// display and nothing else, and it must fail by returning null, never by throwing to the UI.
//
// Each adapter: { id, label, origin, lookup(instructorName, school) -> Promise<{rating, difficulty,
// would_take_again, n, source_url} | null> }.

export const ADAPTERS = {
  rmp: {
    id: "rmp",
    label: "RateMyProfessors",
    origin: "https://www.ratemyprofessors.com/*",
    // The site's own page-search endpoint, called exactly as the site's frontend calls it for a
    // single human search: one request per hover, rate-limited by the background worker.
    async lookup(instructorName, school) {
      const q = encodeURIComponent(`${instructorName} ${school}`.trim());
      const res = await fetch(`https://www.ratemyprofessors.com/search/professors?q=${q}`, { credentials: "omit", cache: "no-store" });
      if (!res.ok) return null;
      const html = await res.text();
      // The page embeds its search results as JSON for hydration; read the first professor node.
      const m = html.match(/"__typename":"Teacher".*?"avgRating":([\d.]+).*?"avgDifficulty":([\d.]+).*?"numRatings":(\d+).*?"wouldTakeAgainPercent":(-?[\d.]+)/s);
      if (!m) return null;
      const wta = Number(m[4]);
      const legacy = html.match(/"legacyId":(\d+)/);
      return {
        rating: Number(m[1]), difficulty: Number(m[2]), n: Number(m[3]),
        would_take_again: wta >= 0 ? wta / 100 : null,
        source_url: legacy ? `https://www.ratemyprofessors.com/professor/${legacy[1]}` : `https://www.ratemyprofessors.com/search/professors?q=${q}`,
      };
    },
  },
};

export const DEFAULT_ADAPTER = "rmp";
