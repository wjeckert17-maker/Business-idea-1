import json, sys, time, datetime, requests
UA = "course-planner-research/0.1 (+mailto:wjeckert17@icloud.com)"
s = requests.Session(); s.headers.update({"User-Agent": UA, "Accept": "application/json"})
page, last = 1, 0.0
while True:
    wait = 1.0 - (time.monotonic() - last)
    if wait > 0: time.sleep(wait)
    last = time.monotonic()
    r = s.get("https://data-c-idsystem.org/api/v1/course-list", params={"page": page, "count": 500, "school_type_id": 0, "school_id": 0, "discipline_id": 0, "descriptor_id": 0, "sort_field": "cid_number", "sort_direction": "asc", "search": ""}, timeout=120)
    if r.status_code != 200:
        print("HTTP", r.status_code, file=sys.stderr); time.sleep(5); continue
    b = r.json(); b["_fetched_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(); b["_url"] = r.url
    json.dump(b, open(f"data/cid/page_{page:03d}.json", "w"))
    if page % 10 == 0: print("page", page, "/", b["last_page"], flush=True)
    if page >= b["last_page"]: break
    page += 1
print("done", page)
