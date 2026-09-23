#!/bin/sh
# Florida SCNS flat file (ASP.NET postback) and Texas TCCNS matrix (.xls). Both public, both ~1 request.
set -e
UA="course-planner-research/0.1 (+mailto:wjeckert17@icloud.com)"
mkdir -p data/scns data/tccns
curl -sS -A "$UA" -c scns_cj.txt "https://flscns.fldoe.org/default" > scns_default.html
python3 - <<'PY'
import re, urllib.parse
h = open("scns_default.html").read()
f = {n: (re.search(r'id="%s" value="([^"]*)"' % n, h) or [None, ""])[1] for n in ("__VIEWSTATE", "__VIEWSTATEGENERATOR")}
f["__EVENTTARGET"] = "ctl00$hl_download"; f["__EVENTARGUMENT"] = ""
open("scns_post.txt", "w").write(urllib.parse.urlencode(f))
PY
curl -sS -A "$UA" -b scns_cj.txt --data @scns_post.txt -o data/scns/crslist.txt "https://flscns.fldoe.org/default"
curl -sS -A "$UA" -o data/scns/data_dictionary.doc "https://flscns.fldoe.org/Downloads/Data_Dictionary_for_Flat_File.doc"
textutil -convert txt -stdout data/scns/data_dictionary.doc > data/scns/data_dictionary.txt 2>/dev/null || true
python3 -c "
import json, sys; sys.path.insert(0, '.')
from canon.sources.scns import institutions_from_dictionary
json.dump(institutions_from_dictionary(open('data/scns/data_dictionary.txt').read()), open('data/scns/institutions.json', 'w'), indent=1)"
# yearid:19 = Fall 2025 - Summer 2026 on tccns.org/download
curl -sS -L -A "$UA" -o data/tccns/matrix_2025.xls "https://www.tccns.org/export/matrix/l:n/yearid:19"
