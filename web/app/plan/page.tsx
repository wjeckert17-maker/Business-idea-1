"use client";
import { useEffect, useMemo, useState } from "react";
import SectionCard from "@/components/SectionCard";
import { useStored } from "@/lib/state";
import { satisfiesIndex } from "@/lib/requirements";
import { backupNote } from "@/lib/explain";
import { gpaClaim, meetingPattern } from "@/lib/format";
import type { Demo, PlanResult } from "@/lib/types";

export default function Plan() {
  const [chosen, , loaded] = useStored<number>("chosen", 1);
  const [demo, setDemo] = useState<Demo | null>(null);
  const [result, setResult] = useState<PlanResult | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { fetch("/api/data").then(r => r.json()).then(setDemo); try { const r = localStorage.getItem("result"); if (r) setResult(JSON.parse(r)); } catch {} }, []);
  const byId = useMemo(() => Object.fromEntries((demo?.sections ?? []).map(s => [s.section_id, s])), [demo]);
  const idx = useMemo(() => (demo ? satisfiesIndex(demo.requirement_tree) : {}), [demo]);
  if (!loaded || !demo) return null;
  const s = result?.schedules.find(x => x.rank === chosen) ?? result?.schedules[0];
  if (!s) return <p className="muted">No schedule chosen yet. Build schedules first, then choose one.</p>;
  const ordered = [...s.sections].sort((a, b) => a.course_code.localeCompare(b.course_code));
  const crns = ordered.map(c => byId[c.section_id]?.crn ?? c.section_id);
  const copy = async () => { try { await navigator.clipboard.writeText(crns.join(" ")); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {} };
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Registration plan</h1>
      <p className="muted text-sm">Schedule #{s.rank} · {s.credits} credits. Enter these CRNs in order; if one is full, use its backup.</p>
      <div className="card p-3">
        <div className="text-xs muted">CRNs</div>
        <div className="mt-1 break-words font-mono text-3xl font-bold leading-tight tracking-wide">{crns.join("  ")}</div>
        <button className="btn mt-3 w-full" onClick={copy}>{copied ? "Copied" : "Copy all CRNs"}</button>
      </div>
      <ol className="space-y-3">
        {ordered.map((c, i) => {
          const sec = byId[c.section_id];
          const alt = c.runner_up ? byId[c.runner_up.section_id] : null;
          return (
            <li key={c.section_id} className="card p-3">
              <div className="flex items-baseline gap-2"><span className="muted">{i + 1}.</span><span className="font-mono text-2xl font-bold">{sec?.crn}</span><span className="text-sm">{c.course_code}</span></div>
              {sec && <div className="mt-2"><SectionCard s={sec} satisfies={idx[c.course_code]} compact /></div>}
              <div className="mt-2 text-sm">
                <div className="font-medium">Backup</div>
                {alt ? (
                  <div><span className="font-mono text-lg font-semibold">{alt.crn}</span> <span className="muted">{meetingPattern(alt.meetings)}{alt.instructor ? ` · ${alt.instructor}` : ""}</span>
                    <div className="muted">{gpaClaim(alt.history)}</div></div>
                ) : null}
                <div className={c.runner_up?.feasible_swap === false ? "warn" : ""}>{backupNote(c, byId)}</div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
