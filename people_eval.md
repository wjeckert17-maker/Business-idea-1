# Instructor identity evaluation

* real SIS instructors: 2334; total mentions (SIS + synthetic grade/rating/syllabus): 29546
* (surname, department) collisions among real instructors: 62
* resolver: {"merges": 9011, "merges_blocked_by_conflict": 252, "merges_blocked_by_ambiguity": 54, "merges_blocked_no_anchor": 332, "persons_opened": 2423, "mentions": 29546, "persons": 2423, "threshold": 0.6, "margin": 0.15}

## Pairwise: precision **1.0000**, recall 0.9956 (tp=275995, fp=0, fn=1227)
## Wrong merges: **0**

| source | mentions | to correct person | to wrong person | left separate | recall |
|---|---|---|---|---|---|
| grade_feed | 5370 | 5330 | 0 | 40 | 0.9926 |
| rating_site | 2334 | 2294 | 0 | 40 | 0.9829 |
| syllabus | 2334 | 2325 | 0 | 9 | 0.9961 |
