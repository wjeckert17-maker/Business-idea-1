# Scoring sanity

* sections with both ratings and grades: 72
* r(raw rating, mean GPA) = **+0.546** (expected clearly positive)
* r(adjusted quality, mean GPA) = **-0.023** (expected within ±0.1)
* r(raw would-take-again, mean GPA) = +0.546; leniency model R² = +0.281
* verdict: **adjustment works: raw r=0.55 → adjusted r=-0.02** → OK

## Demographic skew

* skew audit over 72 labelled sections

| attribute | group | n | mean adjusted quality | mean difficulty | Δ quality vs rest | permutation p |
|---|---|---|---|---|---|---|
| group | A | 36 | +0.166 | 47.9 | +0.333 | 0.354 |
| group | B | 36 | -0.166 | 52.1 | -0.333 | 0.360 |

No group differs from the rest at p<0.05.
