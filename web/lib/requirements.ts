import type { Citation, ReqNode } from "./types";

export type Satisfies = { group: string; citation: Citation | null; path: string[] };

/** For each course code: the nearest requirement group it satisfies, with its citation (nearest ancestor with one). */
export function satisfiesIndex(tree: ReqNode): Record<string, Satisfies> {
  const out: Record<string, Satisfies> = {};
  const walk = (n: ReqNode, path: string[], cite: Citation | null) => {
    const c = n.citation ?? cite;
    if (n.kind === "group") {
      const p = n.title ? [...path, n.title] : path;
      for (const ch of n.children ?? []) walk(ch, p, c);
    } else if (n.kind === "course" && n.code) {
      out[n.code] ??= { group: path[path.length - 1] ?? "requirements", citation: c, path };
    } else if (n.kind === "course_list") {
      for (const code of n.courses ?? []) out[code] ??= { group: path[path.length - 1] ?? "requirements", citation: c, path };
    }
  };
  walk(tree, [], null);
  return out;
}

export function citationText(s: Satisfies | undefined): string {
  if (!s) return "does not map to a listed requirement";
  const cite = s.citation ? ` — ${s.citation.catalog}${s.citation.page ? `, p. ${s.citation.page}` : ""}` : " — no catalog citation on file";
  return `satisfies ${s.group}${cite}`;
}
