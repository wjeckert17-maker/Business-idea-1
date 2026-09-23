/** Parse a pasted transcript or DegreeWorks export into course codes. Pure, no I/O. */
export type Parsed = { code: string; grade: string | null; line: string; confidence: "high" | "medium" };

const CODE = /\b([A-Z]{2,4})\s?-?\s?(\d{3,4}[A-Z]?)\b/g;
const GRADE = /(?<![A-Za-z])(A[+-]?|B[+-]?|C[+-]?|D[+-]?|F|S|U|P|W|IP|T|TR|CR)(?![A-Za-z+-])/;
const NOISE = new Set(["GPA", "FALL", "SPRING", "SUMMER", "TERM", "CREDIT", "HRS", "ID", "USA", "PDF"]);

export function parseTranscript(text: string): Parsed[] {
  const out: Parsed[] = [];
  const seen = new Set<string>();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    for (const m of line.matchAll(CODE)) {
      const subj = m[1], num = m[2];
      if (NOISE.has(subj) || /^\d{4}$/.test(num) && +num > 2100 && +num < 2000) continue;
      const code = `${subj} ${num}`;
      if (seen.has(code)) continue;
      const after = line.slice((m.index ?? 0) + m[0].length);
      const g = after.match(GRADE);
      const grade = g ? g[1] : null;
      // DegreeWorks marks in-progress and withdrawn work; those are not completed
      if (grade && ["IP", "W"].includes(grade)) continue;
      const failing = grade && ["F", "U"].includes(grade);
      if (failing) continue;
      seen.add(code);
      out.push({ code, grade, line, confidence: grade ? "high" : "medium" });
    }
  }
  return out;
}
