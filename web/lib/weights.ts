/** "Learn it" (0) ↔ "Survive it" (1) → quality/difficulty weights. Middle is 1/1. Plain module: used by client and server. */
export function learnToWeights(t: number): { quality: number; difficulty: number } {
  return { quality: +(2 * (1 - t)).toFixed(2), difficulty: +(2 * t).toFixed(2) };
}
