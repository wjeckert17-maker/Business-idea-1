import type { History, Meeting, Section } from "./types";

export const DAYS = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const DAY_SHORT = ["", "M", "T", "W", "R", "F", "S", "U"];

export function hm(min: number): string {
  const h = Math.floor(min / 60), m = min % 60;
  const ampm = h >= 12 ? "pm" : "am";
  const hh = ((h + 11) % 12) + 1;
  return m ? `${hh}:${String(m).padStart(2, "0")}${ampm}` : `${hh}${ampm}`;
}

export function meetingPattern(ms: Meeting[]): string {
  const byTime = new Map<string, number[]>();
  for (const m of ms) { const k = `${m.start}-${m.end}`; byTime.set(k, [...(byTime.get(k) ?? []), m.weekday]); }
  return [...byTime.entries()].map(([k, days]) => { const [s, e] = k.split("-").map(Number); return `${days.sort().map(d => DAY_SHORT[d]).join("")} ${hm(s)}–${hm(e)}`; }).join(", ");
}

/** "3.11 avg GPA (n=880 over 8 terms)" — the sample size is part of the claim, never optional. */
export function gpaClaim(h: History): string {
  if (!h.grade_n || h.mean_gpa == null) return "no grade history for this instructor in this course";
  const thin = h.grade_n < 30 ? " — thin sample, weak evidence" : "";
  return `${h.mean_gpa.toFixed(2)} avg GPA (n=${h.grade_n} over ${h.grade_terms} term${h.grade_terms === 1 ? "" : "s"})${thin}`;
}
export function withdrawClaim(h: History): string {
  if (!h.grade_n || h.w_rate == null) return "";
  return `${(h.w_rate * 100).toFixed(1)}% withdrew (n=${h.grade_n})`;
}
export function ratingClaim(h: History): string {
  if (!h.rating_n || h.rating == null) return "no ratings on file";
  const thin = h.rating_n < 15 ? " — thin sample" : "";
  return `${h.rating.toFixed(1)}/5 rating (n=${h.rating_n})${thin}`;
}
export function confidenceLabel(c: number): string {
  return c >= 0.75 ? "high confidence" : c >= 0.4 ? "moderate confidence" : "low confidence";
}
export function lowConfidenceNote(s: Section): string | null {
  if (!s.low_confidence_sources?.length) return null;
  const parts = s.low_confidence_sources.map(src => src === "grades" ? `grade history is thin (n=${s.history.grade_n})` : src === "sentiment" ? `rating data is thin (n=${s.history.rating_n})` : "no syllabus on file");
  return `Score leans on the department prior: ${parts.join("; ")}.`;
}
export function fullness(s: Section): { pct: number | null; text: string } {
  if (!s.capacity || s.enrolled == null) return { pct: null, text: "seats unknown" };
  const pct = s.enrolled / s.capacity;
  return { pct, text: `${s.enrolled}/${s.capacity} seats filled${pct >= 0.9 ? " — nearly full" : ""}` };
}
