import type { Schedule, ChosenSection, Section } from "./types";

const NAMES: Record<string, string> = {
  quality: "teaching quality", difficulty: "lighter workload", days_on_campus: "fewer days on campus", idle_gap: "shorter gaps",
  start_time: "start time", unlock: "unlocking future courses", fullness: "seat availability",
};
const list = (xs: string[]) => xs.map(x => NAMES[x] ?? x).join(xs.length > 2 ? ", " : " and ");
const pts = (d: number) => (Math.abs(d) < 0.05 ? "under 0.05 points" : Math.abs(d) < 0.1 ? `${Math.abs(d).toFixed(2)} points` : `${Math.abs(d).toFixed(1)} points`);

/** One plain sentence on why the schedule ranked where it did, from the engine's won/lost lists. */
export function rankSentence(s: Schedule, all: Schedule[]): string {
  const { won, lost } = s.explanation;
  const n = all.length;
  const gap = s.rank < n ? pts(s.total_points - all[s.rank].total_points) : null;
  if (s.rank === 1) {
    if (won.length && lost.length) return `Ranked first because it is best on ${list(won)}, even though it is weakest on ${list(lost)}${gap ? `; it beats #2 by ${gap}` : ""}.`;
    if (won.length) return `Ranked first because it is best on ${list(won)}${gap ? ` and beats #2 by ${gap}` : ""}.`;
    return `Ranked first on the combined score${gap ? `, ${gap} ahead of #2` : ""}.`;
  }
  const ahead = pts(all[0].total_points - s.total_points);
  if (won.length && lost.length) return `Ranked #${s.rank}: best on ${list(won)} but weakest on ${list(lost)}, which costs it ${ahead} against #1.`;
  if (won.length) return `Ranked #${s.rank}: best on ${list(won)}, but ${ahead} behind #1 overall.`;
  if (lost.length) return `Ranked #${s.rank} because it is weakest on ${list(lost)}, ${ahead} behind #1.`;
  return `Ranked #${s.rank}, ${ahead} behind #1 with no single objective won or lost.`;
}

export function backupNote(c: ChosenSection, byId: Record<string, Section>): string {
  const r = c.runner_up;
  if (!r) return "No other section of this course is offered this term; if this one fills, the course has no backup.";
  const alt = byId[r.section_id];
  const who = alt?.instructor ? ` with ${alt.instructor}` : "";
  const delta = r.delta_total_points === 0 ? "scores the same" : `scores ${Math.abs(r.delta_total_points).toFixed(1)} points ${r.delta_total_points < 0 ? "lower" : "higher"}`;
  return r.feasible_swap
    ? `CRN ${alt?.crn ?? r.section_id}${who} ${delta} under your weights and fits the rest of the schedule without conflicts.`
    : `CRN ${alt?.crn ?? r.section_id}${who} is the next-best section but ${clashText(r, byId)}; only use it if you also move that.`;
}

function clashText(r: NonNullable<ChosenSection["runner_up"]>, byId: Record<string, Section>): string {
  const parts: string[] = [];
  for (const id of r.clashes_with ?? []) { const s = byId[id]; parts.push(s ? `meets at the same time as ${s.course_code} (CRN ${s.crn})` : `clashes with section ${id}`); }
  if (r.blocked_by?.length) parts.push(`falls inside time you blocked`);
  return parts.length ? parts.join(" and ") : r.why_not;
}
