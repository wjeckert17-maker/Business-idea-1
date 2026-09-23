import { NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import demo from "@/data/demo.json";
import type { Meeting, Prefs, Profile, WeightVector } from "@/lib/types";
import { learnToWeights } from "@/lib/weights";

const ROOT = path.resolve(process.cwd(), "..");

function weights(p: Prefs) {
  const base = learnToWeights(p.learn);
  const def: WeightVector = {
    quality: base.quality, difficulty: base.difficulty, days_on_campus: p.fewerDays, idle_gap: p.avoidGaps,
    start_time: p.startPref === "none" ? 0 : p.startWeight, start_preference: p.startPref === "later" ? "later" : "earlier",
    unlock: p.unlock, fullness: p.avoidFull,
  };
  const per_course: Record<string, WeightVector> = {};
  for (const [code, t] of Object.entries(p.perCourse ?? {})) per_course[code] = { ...def, ...learnToWeights(t) };
  return { default: def, per_course };
}

export async function POST(req: Request) {
  const body = (await req.json()) as { profile: Profile; prefs: Prefs; k?: number };
  const { profile, prefs } = body;
  const request = {
    completed: profile.completed,
    courses: Object.fromEntries(Object.entries(demo.courses).map(([k, c]) => [k, { credits: c.credits, prereqs: c.prereqs, title: c.title }])),
    sections: demo.sections.map(s => ({ section_id: s.section_id, course_code: s.course_code, meetings: s.meetings as Meeting[], instructor: s.instructor,
      capacity: s.capacity, enrolled: s.enrolled, quality: s.quality, difficulty: s.difficulty, credits: s.credits })),
    requirement_tree: demo.requirement_tree,
    constraints: { min_credits: prefs.minCredits, max_credits: prefs.maxCredits, blocked: prefs.blocked, required_courses: [], full_threshold: 0.9 },
    weights: weights(prefs), k: body.k ?? 5, time_limit_s: 8,
  };
  const python = process.env.PLANNER_PYTHON ?? "python3";
  const env = { ...process.env, PYTHONPATH: [ROOT, process.env.PLANNER_PYTHONPATH ?? ""].filter(Boolean).join(path.delimiter), PYTHONWARNINGS: "ignore" };
  const out = await new Promise<{ code: number | null; stdout: string; stderr: string }>(resolve => {
    const p = spawn(python, ["-m", "sched.serve"], { cwd: ROOT, env });
    let stdout = "", stderr = "";
    p.stdout.on("data", d => (stdout += d)); p.stderr.on("data", d => (stderr += d));
    p.on("close", code => resolve({ code, stdout, stderr }));
    p.stdin.end(JSON.stringify(request));
  });
  if (out.code !== 0) return NextResponse.json({ error: `planner failed (exit ${out.code}): ${out.stderr.slice(-800)}` }, { status: 500 });
  return NextResponse.json(JSON.parse(out.stdout));
}
