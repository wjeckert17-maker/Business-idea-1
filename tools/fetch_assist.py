"""Raw-cache fetch of ASSIST AllDepartments agreements. 1 req/s, honest UA, XSRF cookie echo."""
import json, os, sys, time, datetime
import requests
UA = "course-planner-research/0.1 (+mailto:wjeckert17@icloud.com)"
OUT = "data/assist"
s = requests.Session(); s.headers["User-Agent"] = UA
s.get("https://assist.org/", timeout=30)
s.headers["X-XSRF-TOKEN"] = s.cookies.get("X-XSRF-TOKEN"); s.headers["Accept"] = "application/json"
last = 0.0
def get(path, **params):
    global last
    for attempt in range(8):
        wait = 3.0 - (time.monotonic() - last)
        if wait > 0: time.sleep(wait)
        last = time.monotonic()
        try:
            r = s.get("https://assist.org/api/" + path, params=params, timeout=120)
        except requests.RequestException as e:
            print("ERR", e, file=sys.stderr); time.sleep(2 ** attempt); continue
        if r.status_code in (429, 500, 502, 503, 504):
            ra = r.headers.get("Retry-After")
            delay = float(ra) if ra and ra.isdigit() else 90.0 * (attempt + 1)
            print("HTTP", r.status_code, "retry-after", ra, "sleeping", delay, file=sys.stderr, flush=True); time.sleep(delay); continue
        return r
    raise SystemExit("gave up on " + path)
inst = get("institutions").json()
json.dump(inst, open(f"{OUT}/institutions.json", "w"))
years = get("AcademicYears").json(); json.dump(years, open(f"{OUT}/academic_years.json", "w"))
def find(name): return next(i["id"] for i in inst if any(n["name"] == name for n in i["names"]))
receivers = {find("San Jose State University"): "SJSU", find("University of California, Davis"): "UCD"}
plan = []  # (recv, send, year)
for recv in receivers:
    ag = get(f"institutions/{recv}/agreements").json()
    json.dump(ag, open(f"{OUT}/agreements_{recv}.json", "w"))
    for a in ag:
        send = a["institutionParentId"]
        if 74 in a["sendingYearIds"]: plan.append((recv, send, 74))
    # a smaller slice of the newest published year, for as-of demonstrations
    for a in ag[:25]:
        send = a["institutionParentId"]
        if 77 in a["sendingYearIds"]: plan.append((recv, send, 77))
plan = sorted(set(plan))
print("plan size", len(plan), flush=True)
manifest = []
for n, (recv, send, year) in enumerate(plan, 1):
    key = f"{year}/{send}/to/{recv}/AllDepartments"
    fn = f"{OUT}/agreement_{year}_{send}_to_{recv}.json"
    if os.path.exists(fn): continue
    r = get("articulation/Agreements", Key=key)
    body = r.json()
    body["_fetched_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    body["_url"] = r.url
    json.dump(body, open(fn, "w"))
    ok = body.get("isSuccessful")
    manifest.append({"key": key, "file": fn, "ok": ok, "bytes": len(r.content)})
    if n % 20 == 0: print(f"{n}/{len(plan)} fetched", flush=True)
json.dump(manifest, open(f"{OUT}/manifest.json", "w"), indent=1)
print("done", len(manifest))
