# Matcher evaluation — model 7 (all-MiniLM-L6-v2)

## Protocol

* Ground truth pairs were split 80/20 by a salted hash of the directed pair key before any training.
* Negatives exist only inside coverage universes (ASSIST agreements, SCNS/TCCNS institution sets) or explicit denials.
  A pair the ground truth is silent about is 'unknown': never trained on, never counted in precision.
* Threshold chosen on a validation slice of the *train* split to reach the target precision; holdout numbers below are untouched by that choice.
* Recall counts holdout positives the candidate generator never surfaced as false negatives.

## Training

* queries: 14825, labeled train pairs: 297028 (target precision 0.98)
* course targets: fit 4610 pos / 23050 neg; threshold 0.9777 (target **NOT MET**); validation P=0.897 R=0.022 F1=0.044 (tp=35 fp=4 fn=1518, unverifiable=0)
* hub targets: fit 7435 pos / 24459 neg; threshold 0.9729 (target met); validation P=0.981 R=0.020 F1=0.040 (tp=51 fp=1 fn=2444, unverifiable=0)

## Holdout

* holdout pairs scored: 66437; positives missed by candidate generation: 220
* **at the per-family thresholds {'course': 0.9777, 'hub': 0.9729}: P=1.000 R=0.020 F1=0.040 (tp=48 fp=0 fn=2318, unverifiable=22)**

* course targets: P=1.000 R=0.016 F1=0.032 (tp=15 fp=0 fn=903, unverifiable=4)
* hub targets: P=1.000 R=0.023 F1=0.045 (tp=33 fp=0 fn=1415, unverifiable=18)

| threshold (both families) | precision | recall | F1 | tp | fp | fn |
|---|---|---|---|---|---|---|
| 0.5 | 0.462 | 0.598 | 0.521 | 1415 | 1650 | 951 |
| 0.8 | 0.688 | 0.356 | 0.469 | 842 | 382 | 1524 |
| 0.9 | 0.760 | 0.230 | 0.354 | 545 | 172 | 1821 |
| 0.95 | 0.818 | 0.121 | 0.211 | 287 | 64 | 2079 |
| 0.98 | 1.000 | 0.012 | 0.023 | 28 | 0 | 2338 |
| 0.99 | 1.000 | 0.005 | 0.009 | 11 | 0 | 2355 |

### Per ground-truth source (holdout, at threshold)

| source | precision | recall | tp | fp | fn |
|---|---|---|---|---|---|
| assist | 1.000 | 0.016 | 15 | 0 | 903 |
| cid | 1.000 | 0.024 | 14 | 0 | 572 |
| scns | 1.000 | 0.003 | 1 | 0 | 396 |
| tccns | 1.000 | 0.039 | 18 | 0 | 447 |

## Ablation: text-only (course-number and subject features disabled)

* thresholds {'course': 0.9251, 'hub': 0.7307}: P=0.583 R=0.015 F1=0.029 (tp=35 fp=25 fn=2331, unverifiable=11)

* course targets: P=0.486 R=0.018 F1=0.036 (tp=17 fp=18 fn=901, unverifiable=4)
* hub targets: P=0.720 R=0.012 F1=0.024 (tp=18 fp=7 fn=1430, unverifiable=7)

| source | precision | recall | tp | fp | fn |
|---|---|---|---|---|---|
| assist | 0.809 | 0.018 | 17 | 4 | 901 |
| cid | 1.000 | 0.003 | 2 | 0 | 584 |
| scns | 0.471 | 0.040 | 16 | 18 | 381 |
| tccns | 0.000 | 0.000 | 0 | 3 | 465 |

## Worst false positives (holdout)


## Lowest-scoring false negatives (holdout)

* 0.003  : AGRI 0164 — Sustainable Tree Care  →  CID hub: AGEH 130 000 X — Introduction to Tree Care and Urban Forestry  [cid]  drags: num_logdiff=-3.39, subject_affinity=-1.18, num_last3=-0.38
* 0.009  : CS 2180 — Logic and Computing  →  CID hub: PHIL 210 — Symbolic Logic  [cid]  drags: subject_affinity=-3.61, num_logdiff=-0.75, num_last3=-0.38
* 0.009  : SPN 1112 — Elementary Spanish II & III  →  CID hub: SPAN 110 — Elementary Spanish II  [cid]  drags: seq_conflict=-1.80, subject_affinity=-1.27, emb_cos=-0.87
* 0.009  : MATH 230 — Logic and Mathematical Reasoning  →  CID hub: PHIL 210 — Symbolic Logic  [cid]  drags: subject_affinity=-4.34, num_len_equal=-0.60, num_last3=-0.38
* 0.010  : PLT 2420L — Plant Materials Lab  →  CID hub: AGEH 112 112 L — Plant Materials and Usage II  [cid]  drags: num_logdiff=-3.28, subject_affinity=-0.86, num_last3=-0.38
* 0.011  Santa Ana College: ENGL 104 — Language and Culture  →  CID hub: ANTH 130 — Introduction to Linguistic Anthropology  [cid]  drags: subject_affinity=-4.24, jaccard=-0.70, num_len_equal=-0.60
* 0.012  : MUS 281 — Concert Choir I  →  CID hub: MUS 155 — Musicianship IV  [cid]  drags: seq_conflict=-1.80, subject_equal=-1.45, jaccard=-0.70
* 0.015  : NURSING NURS 630 — Introduction to Medical Terminology  →  CID hub: HIT 103 X — Medical Terminology  [cid]  drags: subject_affinity=-2.63, emb_cos=-0.70, num_len_equal=-0.60
* 0.017  FLORIDA INTERNATIONAL UNIVERSITY: ANT 3034 — Anthropological Theories  →  SCNS hub: ANT 034 — History Of Anthropological Theory  [scns]  drags: subject_equal=-1.45, num_logdiff=-1.02, emb_cos=-0.42
* 0.018  Lone Star College System: BIOL 2421 — Microbiology for Science Major I (lecture + lab)  →  TCCNS hub: BIOL 2121 — Microbiology for Science Majors (lab)  [tccns]  drags: subject_equal=-1.45, emb_cos=-0.63, num_len_equal=-0.60

## Positives never surfaced as candidates (sample)

* FLORIDA ATLANTIC UNIVERSITY: MAR 6055 — Marketing Functions And Processes  →  SCNS hub: MAR 055 — Grad Introduction To Marketing  [scns]
* Oxnard College: ENGL R102 — Critical Thinking through Composition and Literature  →  San Jose State University: ENGL 1B — Argument and Analysis  [assist]
* Oxnard College: ENGL R102 — Critical Thinking through Composition and Literature  →  University of California, Davis: ENL 003 — Introduction to Literature  [assist]
* Texas Christian University: ENGR 1020 — Introduction to Digital Systems (Lecture + Lab)  →  TCCNS hub: ENGR 2333 — Elementary Chemical Engineering  [tccns]
* San Diego City College: ENGL 211 — American Literature II  →  San Jose State University: ENGL 70 — Emerging Modernisms and Beyond  [assist]
* : ENGL 6 — Creative Writing1  →  CID hub: ENGL 160 — Survey of British Literature 1  [cid]
* UNIVERSITY OF WEST FLORIDA: STA 2023 — Honors Elements Of Statistics  →  SCNS hub: STA 023 — Statistical Methods I (Ge Core)  [scns]
* Mission College: ART 033A — Basic Design: Two-Dimensional  →  University of California, Davis: DES 015 — Form & Color  [assist]
* Oxnard College: ENGL R104 — English Literature I  →  San Jose State University: ENGL 50 — Beginnings to the American Experiment  [assist]
* College of the Desert: ART 3A — Basic Design & Color  →  San Jose State University: DSGD 63 — Fundamental Graphic Visualization  [assist]
