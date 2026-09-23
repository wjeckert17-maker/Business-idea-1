"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import WeekGrid from "@/components/WeekGrid";
import WeightSliders from "@/components/WeightSliders";
import { DEFAULT_PREFS, DEFAULT_PROFILE, useStored } from "@/lib/state";
import { satisfiesIndex } from "@/lib/requirements";
import type { Demo, Prefs, Profile } from "@/lib/types";

export default function Constraints() {
  const [prefs, setPrefs, loaded] = useStored<Prefs>("prefs", DEFAULT_PREFS);
  const [profile] = useStored<Profile>("profile", DEFAULT_PROFILE);
  const [demo, setDemo] = useState<Demo | null>(null);
  useEffect(() => { fetch("/api/data").then(r => r.json()).then(setDemo); }, []);
  const courses = useMemo(() => {
    if (!demo) return [];
    const idx = satisfiesIndex(demo.requirement_tree);
    const offered = new Set(demo.sections.map(s => s.course_code));
    return Object.keys(idx).filter(c => offered.has(c) && !profile.completed.includes(c)).sort()
      .map(c => ({ code: c, title: demo.courses[c]?.title ?? "", group: `${idx[c].group}` }));
  }, [demo, profile.completed]);
  if (!loaded) return null;
  const Row = ({ label, k, max = 2, step = 0.1 }: { label: string; k: keyof Prefs; max?: number; step?: number }) => (
    <label className="block text-sm"><div className="flex justify-between"><span>{label}</span><span className="muted">{(prefs[k] as number) === 0 ? "off" : `×${(prefs[k] as number).toFixed(1)}`}</span></div>
      <input type="range" min={0} max={max} step={step} value={prefs[k] as number} onChange={e => setPrefs({ ...prefs, [k]: +e.target.value })} /></label>
  );
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Constraints</h1>
      <section>
        <h2 className="font-semibold">Blocked time</h2>
        <p className="muted text-sm">Work shifts, practice, commute. Drag to block; drag again to clear.</p>
        <div className="card mt-2 p-2"><WeekGrid blocked={prefs.blocked} onChange={b => setPrefs({ ...prefs, blocked: b })} /></div>
      </section>
      <section className="card p-3 space-y-2">
        <h2 className="font-semibold">Credits</h2>
        <div className="flex items-center gap-3 text-sm">
          <label>min <input type="number" className="card w-16 p-2" value={prefs.minCredits} onChange={e => setPrefs({ ...prefs, minCredits: +e.target.value })} /></label>
          <label>max <input type="number" className="card w-16 p-2" value={prefs.maxCredits} onChange={e => setPrefs({ ...prefs, maxCredits: +e.target.value })} /></label>
        </div>
      </section>
      <section>
        <h2 className="font-semibold">Learn it or survive it</h2>
        <p className="muted text-sm">Quality means the section's leniency-adjusted teaching signal; workload means its difficulty percentile in its department.</p>
        <div className="mt-2"><WeightSliders prefs={prefs} setPrefs={setPrefs} courses={courses} /></div>
      </section>
      <section className="card space-y-3 p-3">
        <h2 className="font-semibold">Whole-schedule preferences</h2>
        <Row label="Fewer days on campus" k="fewerDays" />
        <Row label="Shorter gaps between classes" k="avoidGaps" />
        <Row label="Avoid sections above 90% full" k="avoidFull" />
        <Row label="Prefer courses that unlock more later" k="unlock" />
        <div className="text-sm"><div>Start times</div>
          <div className="mt-1 flex gap-2">{(["none", "earlier", "later"] as const).map(v => (
            <button key={v} className={`btn ${prefs.startPref === v ? "" : "btn-secondary"} flex-1`} onClick={() => setPrefs({ ...prefs, startPref: v, startWeight: v === "none" ? 0 : Math.max(prefs.startWeight, 1) })}>{v === "none" ? "no preference" : v}</button>
          ))}</div>
          {prefs.startPref !== "none" && <Row label="How much it matters" k="startWeight" />}
        </div>
      </section>
      <Link href="/results" className="btn w-full">Build my schedules</Link>
    </div>
  );
}
