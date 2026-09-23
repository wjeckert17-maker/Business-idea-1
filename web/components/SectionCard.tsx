import type { Section } from "@/lib/types";
import { confidenceLabel, fullness, gpaClaim, lowConfidenceNote, meetingPattern, ratingClaim, withdrawClaim } from "@/lib/format";
import Citation from "./Citation";
import type { Satisfies } from "@/lib/requirements";

/** Instructor data is context for the section, never a ranking of a person. Every figure carries its n. */
export default function SectionCard({ s, satisfies, compact = false }: { s: Section; satisfies?: Satisfies; compact?: boolean }) {
  const low = lowConfidenceNote(s);
  const seats = fullness(s);
  return (
    <div className="card p-3">
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <div className="font-semibold">{s.course_code} <span className="muted font-normal">§{s.section_code}</span></div>
          <div className="text-sm">{s.title}</div>
        </div>
        <div className="text-right text-sm"><div>{s.credits ?? "?"} cr</div><div className="muted">CRN {s.crn}</div></div>
      </div>
      <div className="mt-1 text-sm">{meetingPattern(s.meetings)}{s.instructor ? ` · ${s.instructor}` : " · instructor TBA"}</div>
      <Citation s={satisfies} />
      {!compact && (
        <div className="mt-2 space-y-1 text-sm">
          <div><span className="muted">Historical grades, this instructor in this course:</span> {gpaClaim(s.history)}{withdrawClaim(s.history) ? `; ${withdrawClaim(s.history)}` : ""}</div>
          <div><span className="muted">Student ratings:</span> {ratingClaim(s.history)}</div>
          <div><span className="muted">Seats:</span> {seats.text}</div>
          <div><span className="muted">Score inputs:</span> {confidenceLabel(s.confidence)} ({(s.confidence * 100).toFixed(0)}% of the score comes from data rather than the department prior)</div>
          {low && <div className="warn">{low}</div>}
        </div>
      )}
    </div>
  );
}
