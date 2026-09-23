"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ScheduleWeek from "@/components/ScheduleWeek";
import SectionCard from "@/components/SectionCard";
import { DEFAULT_PREFS, DEFAULT_PROFILE, useStored } from "@/lib/state";
import { satisfiesIndex } from "@/lib/requirements";
import { rankSentence } from "@/lib/explain";
import type { Demo, PlanResult, Prefs, Profile, Schedule } from "@/lib/types";

export default function Results() {
  const [prefs, , pl] = useStored<Prefs>("prefs", DEFAULT_PREFS);
  const [profile, , pr] = useStored<Profile>("profile", DEFAULT_PROFILE);
  const [, setChosen] = useStored<number>("chosen", 1);
  const [demo, setDemo] = useState<Demo | null>(null);
  const [result, setResult] = useState<PlanResult | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { fetch("/api/data").then(r => r.json()).then(setDemo); }, []);
  useEffect(() => {
    if (!pl || !pr) return;
    setBusy(true);
    fetch("/api/plan", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ profile, prefs }) })
      .then(async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { schedules: [], feasible: false, relaxation: null, candidate_courses: [], excluded_courses: {}, solver_status: "error", error: `planner returned HTTP ${r.status}: ${t.slice(0, 300)}` }; } })
      .then(r => { setResult(r); if (!r.error) { try { localStorage.setItem("result", JSON.stringify(r)); } catch {} } }).finally(() => setBusy(false));
  }, [pl, pr]); // eslint-disable-line react-hooks/exhaustive-deps
  const byId = useMemo(() => Object.fromEntries((demo?.sections ?? []).map(s => [s.section_id, s])), [demo]);
  const idx = useMemo(() => (demo ? satisfiesIndex(demo.requirement_tree) : {}), [demo]);
  if (busy || !demo) return <p className="muted">Solving… the engine is searching for five different course combinations.</p>;
  if (!result) return null;
  if (result.error) return <p className="warn">{result.error}</p>;
  const stat = (s: Schedule) => {
    const secs = s.sections.map(c => byId[c.section_id]).filter(Boolean);
    const withG = secs.filter(x => x.history.grade_n > 0);
    const n = withG.reduce((a, x) => a + x.history.grade_n, 0);
    const gpa = n ? withG.reduce((a, x) => a + (x.history.mean_gpa ?? 0) * x.history.grade_n, 0) / n : null;
    const low = secs.filter(x => x.low_confidence_sources?.length).length;
    return { secs, gpa, n, days: s.explanation.metrics.days_on_campus, low, missing: secs.length - withG.length };
  };
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Your schedules</h1>
      {result.relaxation && (
        <div className="card warn p-3 text-sm">No schedule met every constraint. The engine dropped the cheapest set — <b>{result.relaxation.relaxed.join(", ")}</b> — to find these. {result.relaxation.reason}</div>
      )}
      {result.schedules.length === 0 && <p className="warn">No schedule could be built. {Object.keys(result.excluded_courses).length} courses were excluded; check completed courses and credit limits.</p>}
      <p className="muted text-sm">Swipe sideways. {result.schedules.length} schedules, each a different set of courses.</p>
      <div className="snap -mx-4 flex gap-3 overflow-x-auto px-4 pb-2">
        {result.schedules.map(s => {
          const st = stat(s);
          return (
            <article key={s.rank} className="card w-[88vw] max-w-md shrink-0 p-3">
              <header className="flex items-baseline justify-between"><h2 className="text-lg font-bold">#{s.rank}</h2><span className="muted text-sm">{s.total_points.toFixed(1)} pts</span></header>
              <p className="mt-1 text-sm">{rankSentence(s, result.schedules)}</p>
              <dl className="mt-2 grid grid-cols-3 gap-2 text-center text-sm">
                <div className="card p-2"><dt className="muted text-xs">credits</dt><dd className="font-semibold">{s.credits}</dd></div>
                <div className="card p-2"><dt className="muted text-xs">avg instructor GPA</dt><dd className="font-semibold">{st.gpa == null ? "no data" : `${st.gpa.toFixed(2)}`}</dd><dd className="text-xs muted">{st.n ? `n=${st.n}${st.missing ? `, ${st.missing} no history` : ""}` : ""}</dd></div>
                <div className="card p-2"><dt className="muted text-xs">days on campus</dt><dd className="font-semibold">{st.days}</dd></div>
              </dl>
              {st.low > 0 && <p className="warn mt-2 text-sm">{st.low} of {st.secs.length} sections scored on thin data; details below.</p>}
              <div className="mt-3"><ScheduleWeek sections={st.secs} /></div>
              <div className="mt-3 space-y-2">{st.secs.map(x => <SectionCard key={x.section_id} s={x} satisfies={idx[x.course_code]} />)}</div>
              <Link href="/plan" onClick={() => setChosen(s.rank)} className="btn mt-3 w-full">Choose #{s.rank}</Link>
            </article>
          );
        })}
      </div>
      {Object.keys(result.excluded_courses).length > 0 && (
        <details className="card p-3 text-sm"><summary className="cursor-pointer font-medium">Courses not considered ({Object.keys(result.excluded_courses).length})</summary>
          <ul className="mt-2 space-y-1">{Object.entries(result.excluded_courses).map(([c, why]) => <li key={c}><b>{c}</b> <span className="muted">{why}</span></li>)}</ul></details>
      )}
    </div>
  );
}
