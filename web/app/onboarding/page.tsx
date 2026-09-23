"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { DEFAULT_PROFILE, useStored } from "@/lib/state";
import { parseTranscript } from "@/lib/transcript";
import { satisfiesIndex, citationText } from "@/lib/requirements";
import type { Demo, Profile } from "@/lib/types";

export default function Onboarding() {
  const [profile, setProfile, loaded] = useStored<Profile>("profile", DEFAULT_PROFILE);
  const [demo, setDemo] = useState<Demo | null>(null);
  const [paste, setPaste] = useState("");
  useEffect(() => { fetch("/api/data").then(r => r.json()).then(setDemo); }, []);
  const parsed = useMemo(() => parseTranscript(paste), [paste]);
  const idx = useMemo(() => (demo ? satisfiesIndex(demo.requirement_tree) : {}), [demo]);
  const known = new Set(Object.keys(demo?.courses ?? {}));
  if (!loaded) return null;
  const add = () => setProfile({ ...profile, completed: Array.from(new Set([...profile.completed, ...parsed.map(p => p.code)])) });
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">About you</h1>
      {demo && <p className="text-sm muted">{demo.term.institution} · planning {demo.term.name}. <span className="warn">{demo.data_note}</span></p>}
      <label className="block"><span className="text-sm font-medium">Major</span>
        <input className="card mt-1 w-full p-3" value={profile.major} onChange={e => setProfile({ ...profile, major: e.target.value })} /></label>
      <label className="block"><span className="text-sm font-medium">Minors (comma separated)</span>
        <input className="card mt-1 w-full p-3" value={profile.minors.join(", ")} onChange={e => setProfile({ ...profile, minors: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} /></label>
      <label className="block"><span className="text-sm font-medium">Catalog year</span>
        <select className="card mt-1 w-full p-3" value={profile.catalogYear} onChange={e => setProfile({ ...profile, catalogYear: e.target.value })}>
          {["2024-25", "2025-26", "2026-27"].map(y => <option key={y}>{y}</option>)}
        </select>
        <span className="cite">Requirements are read from the {profile.catalogYear} catalog you matriculated under.</span></label>

      <section className="space-y-2">
        <h2 className="font-semibold">Completed courses</h2>
        <p className="text-sm muted">Paste your transcript or a DegreeWorks export. Course codes are picked out locally on your phone; nothing is uploaded.</p>
        <textarea className="card w-full p-3 text-sm" rows={5} placeholder={"CS 1301  Intro to Computing   A   3.0\nMATH 1551  Differential Calculus  B+  2.0"} value={paste} onChange={e => setPaste(e.target.value)} />
        {parsed.length > 0 && (
          <div className="card p-3 text-sm">
            <div className="font-medium">Found {parsed.length} course{parsed.length === 1 ? "" : "s"}</div>
            <ul className="mt-1 space-y-1">
              {parsed.map(p => (
                <li key={p.code} className="flex justify-between">
                  <span>{p.code}{p.grade ? ` (${p.grade})` : ""}{!known.has(p.code) && <span className="warn"> — not in this term's catalog data</span>}</span>
                  <span className="muted">{p.confidence === "high" ? "grade seen" : "no grade seen"}</span>
                </li>
              ))}
            </ul>
            <button className="btn mt-3 w-full" onClick={add}>Add these to completed</button>
          </div>
        )}
        {profile.completed.length > 0 && (
          <div className="card p-3 text-sm">
            <div className="font-medium">Completed ({profile.completed.length})</div>
            <ul className="mt-1 space-y-1">
              {profile.completed.map(c => (
                <li key={c} className="flex items-start justify-between gap-2">
                  <div><div>{c}</div><div className="cite">{idx[c] ? citationText(idx[c]) : "not part of a listed requirement"}</div></div>
                  <button className="muted" aria-label={`remove ${c}`} onClick={() => setProfile({ ...profile, completed: profile.completed.filter(x => x !== c) })}>✕</button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
      <Link href="/constraints" className="btn w-full">Next: constraints</Link>
    </div>
  );
}
