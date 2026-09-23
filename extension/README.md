# Planner section scores — Chrome extension (Manifest V3)

```
manifest.json            MV3; storage + two narrow host permissions; rating site is *optional_host_permissions*
background.js            service worker: batched /api/scores by CRN; third-party queue (1 per 2.5 s, queue ≤ 3, hover/click only),
                         chrome.storage.session cache (memory only), no logging, no analytics
content/banner.js        Banner 9 class-search: finds rows by td[data-property="courseReferenceNumber"], injects badges,
                         requests third-party enrichment only on badge hover (400 ms dwell) or click
adapters/thirdparty.js   the single place a rating site is described; add a site = add an adapter + its origin
popup.html / popup.js    settings: own scores (default on), third-party (default off; requests the optional permission on opt-in)
PRIVACY.md               Chrome Web Store privacy disclosure
```

Load unpacked: `chrome://extensions` → Developer mode → Load unpacked → this folder. Point "Planner API" at the running web app.

Before shipping the RateMyProfessors adapter, confirm with counsel that a per-hover, user-initiated request from the user's own browser is acceptable under that site's terms of use; the adapter is isolated so it can be removed without touching the rest.
