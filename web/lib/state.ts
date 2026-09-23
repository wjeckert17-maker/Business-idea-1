"use client";
import { useEffect, useState } from "react";
import type { Prefs, Profile } from "./types";

export const DEFAULT_PROFILE: Profile = { major: "BS Computer Science", minors: [], catalogYear: "2026-27", completed: [] };
export const DEFAULT_PREFS: Prefs = {
  blocked: [], minCredits: 12, maxCredits: 16, learn: 0.5, perCourse: {}, fewerDays: 0, avoidGaps: 0, startPref: "none", startWeight: 0, avoidFull: 0.5, unlock: 0.3,
};

export function useStored<T>(key: string, initial: T): [T, (v: T) => void, boolean] {
  const [value, setValue] = useState<T>(initial);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    try { const raw = localStorage.getItem(key); if (raw) setValue({ ...initial, ...JSON.parse(raw) }); } catch { /* fresh browser */ }
    setLoaded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set = (v: T) => { setValue(v); try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* private mode */ } };
  return [value, set, loaded];
}

export { learnToWeights } from "./weights";
