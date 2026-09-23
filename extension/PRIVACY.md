# Privacy disclosure — Planner section scores (Chrome extension)

*This text is written to be pasted into the Chrome Web Store "Privacy practices" form and shown to users.*

## What the extension does
On the registrar's class-search page (`registration.banner.gatech.edu/StudentRegistrationSsb/ssb/classSearch/*`) it adds a small badge to each section row showing the planner's own quality and difficulty scores, the share of that score that came from data rather than a department prior, and the section's aggregate historical GPA with its sample size. Optionally, and only after you opt in, it shows aggregate rating numbers from a rating site you already use.

## What is fetched, and from where
| data | from | when | sent where |
|---|---|---|---|
| Planner scores for the CRNs visible on the page | the planner API you configure (default `http://localhost:3000`) | when section rows appear on the page | the **CRN numbers only** are sent to the planner API; no page content, no instructor names, no identity |
| Aggregate rating numbers (rating, difficulty, would-take-again, sample size) | the rating site (RateMyProfessors), **directly from your browser** | only when third-party ratings are switched on **and** you hover over or click one badge; one request at a time, at most one every 2.5 seconds; never for a whole page at once | **nowhere**. Rendered on screen only. |

## What is stored
* Your three settings (own scores on/off, third-party on/off, planner API address) in `chrome.storage.sync`.
* Third-party results in `chrome.storage.session`, which is memory only: it is cleared when the browser closes, when you switch third-party ratings off, or after 30 minutes. Nothing third-party is ever written to disk.
* Nothing else. No browsing history, no page contents, no identifiers.

## What leaves your device
* CRN numbers, to the planner API.
* Your rating-site search request, to the rating site — exactly as if you had searched there yourself.
* Nothing to the extension's developer: the extension contains **no analytics, telemetry, error reporting or crash reporting**, and third-party content is never included in any request to the planner.

## Permissions and why
* `storage` — the three settings and the memory-only session cache.
* Host permission for the registrar's class-search pages — to read section rows and add badges. No other pages.
* Host permission for the planner API — to look up scores by CRN.
* **Optional** host permission for the rating site — requested only when you switch third-party ratings on, removed when you switch them off. The extension does not request access to any other site, and never requests `<all_urls>`.

## Failure behaviour
If the planner API is unreachable the badge is not shown. If a third-party lookup fails or is blocked, the badge shows the planner's own scores and simply omits the third-party numbers; no error is displayed and nothing is retried automatically.

## Contact
wjeckert17@icloud.com
