"use client";
import type { Prefs } from "@/lib/types";
import { learnToWeights } from "@/lib/weights";

function LearnSlider({ value, onChange, label, aria }: { value: number; onChange: (v: number) => void; label?: string; aria?: string }) {
  const w = learnToWeights(value);
  return (
    <div>
      {label && <div className="text-sm font-medium">{label}</div>}
      <div className="flex items-center gap-2 text-xs">
        <span className="w-16 shrink-0 whitespace-nowrap">Learn it</span>
        <input type="range" min={0} max={1} step={0.05} value={value} onChange={e => onChange(+e.target.value)} aria-label={`${aria ?? label ?? "default"}: learn it versus survive it`} />
        <span className="w-16 shrink-0 whitespace-nowrap text-right">Survive it</span>
      </div>
      <div className="text-xs muted">weights teaching quality ×{w.quality.toFixed(1)}, lighter workload ×{w.difficulty.toFixed(1)}</div>
    </div>
  );
}

export default function WeightSliders({ prefs, setPrefs, courses }: { prefs: Prefs; setPrefs: (p: Prefs) => void; courses: { code: string; title: string; group: string }[] }) {
  const overrides = prefs.perCourse ?? {};
  return (
    <div className="space-y-4">
      <div className="card p-3"><LearnSlider label="Default for every course" value={prefs.learn} onChange={v => setPrefs({ ...prefs, learn: v })} /></div>
      <details className="card p-3" open={Object.keys(overrides).length > 0}>
        <summary className="cursor-pointer font-medium">Set it per course ({Object.keys(overrides).length} overridden)</summary>
        <p className="muted mt-1 text-sm">Each course keeps its own dial. Unset courses use the default above.</p>
        <div className="mt-3 space-y-3">
          {courses.map(c => {
            const has = c.code in overrides;
            return (
              <div key={c.code} className="border-t pt-2" style={{ borderColor: "var(--line)" }}>
                <div className="flex items-center justify-between">
                  <div><span className="font-medium">{c.code}</span> <span className="muted text-sm">{c.title}</span><div className="cite">{c.group}</div></div>
                  <button className="text-sm" style={{ color: "var(--accent)" }} onClick={() => {
                    const next = { ...overrides }; if (has) delete next[c.code]; else next[c.code] = prefs.learn; setPrefs({ ...prefs, perCourse: next });
                  }}>{has ? "use default" : "customize"}</button>
                </div>
                {has && <LearnSlider aria={c.code} value={overrides[c.code]} onChange={v => setPrefs({ ...prefs, perCourse: { ...overrides, [c.code]: v } })} />}
              </div>
            );
          })}
        </div>
      </details>
    </div>
  );
}
