"use client";
import { useRef, useState } from "react";
import type { Meeting } from "@/lib/types";
import { DAY_SHORT, hm } from "@/lib/format";

const DAYS = [1, 2, 3, 4, 5, 6, 7];
const START = 7 * 60, END = 22 * 60, STEP = 30;
const ROWS = (END - START) / STEP;

function key(d: number, r: number) { return `${d}:${r}`; }

/** Drag (touch or mouse) across cells to block time. Each blocked cell becomes a 30-minute window; adjacent cells merge. */
export default function WeekGrid({ blocked, onChange }: { blocked: Meeting[]; onChange: (b: Meeting[]) => void }) {
  const cells = new Set<string>();
  for (const b of blocked) for (let t = b.start; t < b.end; t += STEP) cells.add(key(b.weekday, (t - START) / STEP));
  const [drag, setDrag] = useState<{ mode: "add" | "remove"; touched: Set<string>; last: string | null } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const cellAt = (x: number, y: number) => {
    const el = document.elementFromPoint(x, y) as HTMLElement | null;
    return el?.dataset?.cell ?? null;
  };
  const commit = (set: Set<string>) => {
    const out: Meeting[] = [];
    for (const d of DAYS) {
      let run: number | null = null;
      for (let r = 0; r <= ROWS; r++) {
        const on = r < ROWS && set.has(key(d, r));
        if (on && run === null) run = r;
        if (!on && run !== null) { out.push({ weekday: d, start: START + run * STEP, end: START + r * STEP }); run = null; }
      }
    }
    onChange(out);
  };
  const apply = (c: string | null, d: { mode: "add" | "remove"; touched: Set<string>; last: string | null }) => {
    if (!c) return;
    // Pointer events arrive sparsely on a fast swipe: fill every cell between the last one and this one.
    const targets: string[] = [c];
    if (d.last) {
      const [ld, lr] = d.last.split(":").map(Number);
      const [cd, cr] = c.split(":").map(Number);
      if (ld === cd) for (let r = Math.min(lr, cr); r <= Math.max(lr, cr); r++) targets.push(key(cd, r));
    }
    d.last = c;
    const fresh = targets.filter(t => !d.touched.has(t));
    if (!fresh.length) return;
    const next = new Set(cells);
    for (const t of fresh) { d.touched.add(t); if (d.mode === "add") next.add(t); else next.delete(t); }
    commit(next);
  };
  const onDown = (e: React.PointerEvent) => {
    const c = cellAt(e.clientX, e.clientY);
    if (!c) return;
    const d = { mode: cells.has(c) ? "remove" as const : "add" as const, touched: new Set<string>(), last: null as string | null };
    setDrag(d); apply(c, d);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => { if (drag) apply(cellAt(e.clientX, e.clientY), drag); };
  const onUp = () => setDrag(null);

  return (
    <div>
      <div ref={ref} className="select-none touch-none" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
        <div className="grid text-xs" style={{ gridTemplateColumns: "2.6rem repeat(7, 1fr)" }}>
          <div />
          {DAYS.map(d => <div key={d} className="py-1 text-center font-medium">{DAY_SHORT[d]}</div>)}
          {Array.from({ length: ROWS }, (_, r) => (
            <div key={r} className="contents">
              <div className="pr-1 text-right muted" style={{ fontSize: 10, lineHeight: "22px" }}>{r % 2 === 0 ? hm(START + r * STEP) : ""}</div>
              {DAYS.map(d => {
                const on = cells.has(key(d, r));
                return <div key={d} data-cell={key(d, r)} className="border-b border-l" style={{ height: 22, borderColor: "var(--line)", background: on ? "var(--accent)" : "transparent", opacity: on ? 0.85 : 1 }} />;
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 text-sm muted">
        {blocked.length === 0 ? "Nothing blocked. Drag across the grid to mark work shifts and commitments." :
          blocked.map(b => `${DAY_SHORT[b.weekday]} ${hm(b.start)}–${hm(b.end)}`).join(" · ")}
      </div>
    </div>
  );
}
