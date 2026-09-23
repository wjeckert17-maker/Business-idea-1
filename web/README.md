# Planner web UI

Next.js 15 + TypeScript + Tailwind 4, mobile-first, on top of the `sched` engine (`python -m sched.serve`) and the
`scoring` service. Screens: `/onboarding`, `/constraints`, `/results`, `/plan`.

```bash
cd web && npm install
PLANNER_PYTHON=python3 PLANNER_PYTHONPATH=/path/with/ortools npm run dev      # http://localhost:3000
```

* `app/api/data` serves `data/demo.json` (built by `tools/build_demo_data.py`: real Fall 2026 Georgia Tech sections,
  a requirement tree with catalog citations, and **synthetic** grade/rating history scored by `scoring`).
* `app/api/plan` turns the stored profile + preferences into a `PlanRequest`, runs the CP-SAT engine in a Python
  subprocess and returns the `PlanResult`.
* State lives in `localStorage` (`profile`, `prefs`, `chosen`, `result`); the transcript parser runs in the browser.

Design rules enforced in components: every course line shows its requirement citation in plain text
(`components/Citation.tsx`); instructor history is worded as section context with `n=` on every figure
(`lib/format.ts`); a section whose score leaned on the department prior says so inline (`lowConfidenceNote`).
