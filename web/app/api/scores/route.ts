import { NextResponse } from "next/server";
import demo from "@/data/demo.json";

/** GET /api/scores?crn=85796&crn=94146 — the extension's lookup. Returns only this app's own
 *  scores and the section's aggregate grade history; never anything about third-party sites. */
const byCrn = new Map(demo.sections.map(s => [s.crn, s]));

const CORS = { "access-control-allow-origin": "*", "access-control-allow-methods": "GET, OPTIONS", "access-control-allow-headers": "content-type" };

export function OPTIONS() { return new NextResponse(null, { headers: CORS }); }

export function GET(req: Request) {
  const url = new URL(req.url);
  const crns = url.searchParams.getAll("crn").slice(0, 100);
  const out: Record<string, unknown> = {};
  for (const crn of crns) {
    const s = byCrn.get(crn);
    if (!s) continue;
    out[crn] = {
      course_code: s.course_code, instructor: s.instructor, quality: s.quality, difficulty: s.difficulty, confidence: s.confidence,
      low_confidence_sources: s.low_confidence_sources,
      history: { mean_gpa: s.history.mean_gpa, grade_n: s.history.grade_n, grade_terms: s.history.grade_terms, w_rate: s.history.w_rate },
    };
  }
  return NextResponse.json({ term: demo.term, data_note: demo.data_note, scores: out }, { headers: CORS });
}
