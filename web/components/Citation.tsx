import { citationText, type Satisfies } from "@/lib/requirements";
export default function Citation({ s }: { s: Satisfies | undefined }) {
  return <p className="cite">{citationText(s)}</p>;
}
