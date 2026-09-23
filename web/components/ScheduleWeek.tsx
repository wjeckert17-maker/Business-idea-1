import type { Section } from "@/lib/types";
import { DAY_SHORT, hm } from "@/lib/format";

const DAYS = [1, 2, 3, 4, 5];
const START = 8 * 60, END = 20 * 60;
const COLORS = ["#1f5f8b", "#8b3a1f", "#3a7d44", "#6b3f8b", "#8b6b1f", "#1f7d8b", "#8b1f5f"];

/** Compact week view of one schedule. */
export default function ScheduleWeek({ sections }: { sections: Section[] }) {
  const span = END - START;
  return (
    <div className="grid gap-px" style={{ gridTemplateColumns: "1.6rem repeat(5, 1fr)" }}>
      <div />
      {DAYS.map(d => <div key={d} className="text-center text-xs font-medium">{DAY_SHORT[d]}</div>)}
      <div className="relative" style={{ height: 300 }}>
        {[8, 10, 12, 14, 16, 18].map(h => <div key={h} className="absolute right-1 text-[10px] muted" style={{ top: ((h * 60 - START) / span) * 300 - 6 }}>{hm(h * 60)}</div>)}
      </div>
      {DAYS.map(d => (
        <div key={d} className="relative rounded" style={{ height: 300, background: "color-mix(in srgb, var(--fg) 4%, transparent)" }}>
          {sections.map((s, i) => s.meetings.filter(m => m.weekday === d).map((m, j) => (
            <div key={`${s.section_id}-${j}`} className="absolute inset-x-0.5 overflow-hidden rounded px-1 text-[10px] leading-tight text-white"
              style={{ top: ((m.start - START) / span) * 300, height: Math.max(14, ((m.end - m.start) / span) * 300), background: COLORS[i % COLORS.length] }}>
              <div className="font-semibold">{s.course_code}</div>
              <div>{hm(m.start)}</div>
            </div>
          )))}
        </div>
      ))}
    </div>
  );
}
