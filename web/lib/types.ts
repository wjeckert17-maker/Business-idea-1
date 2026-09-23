export type Meeting = { weekday: number; start: number; end: number };
export type Citation = { catalog: string; page?: number };

export type History = {
  grade_n: number; grade_terms: number; w_rate: number | null; mean_gpa: number | null; rating_n: number; rating: number | null;
};
export type Section = {
  section_id: string; crn: string; course_code: string; section_code: string; title: string; meetings: Meeting[];
  instructor: string | null; capacity: number | null; enrolled: number | null; credits: number | null; modality: string;
  quality: number; difficulty: number; confidence: number; confidence_by_source: Record<string, number>;
  low_confidence_sources: string[]; history: History; components: { name: string; axis: string; detail: string }[];
};
export type Course = { code: string; title: string; credits: number; prereqs: string[][] };
export type ReqNode = {
  kind: "group" | "course" | "course_list"; title?: string; operator?: string; min_count?: number; code?: string;
  courses?: string[]; children?: ReqNode[]; citation?: Citation;
};
export type Demo = {
  term: { code: string; name: string; institution: string }; data_note: string;
  leniency_model: { slope: number; intercept: number; r_squared: number; n: number } | null;
  courses: Record<string, Course>; sections: Section[]; requirement_tree: ReqNode;
};

export type WeightVector = {
  quality: number; difficulty: number; days_on_campus: number; idle_gap: number; start_time: number;
  start_preference: "earlier" | "later"; unlock: number; fullness: number;
};
export type Profile = { major: string; minors: string[]; catalogYear: string; completed: string[] };
export type Prefs = {
  blocked: Meeting[]; minCredits: number; maxCredits: number; learn: number; perCourse: Record<string, number>;
  fewerDays: number; avoidGaps: number; startPref: "earlier" | "later" | "none"; startWeight: number; avoidFull: number; unlock: number;
};

export type RunnerUp = { section_id: string; score: number; delta_total_points: number; feasible_swap: boolean; clashes_with?: string[]; blocked_by?: string[]; why_not: string } | null;
export type ChosenSection = { course_code: string; section_id: string; score: number; weights: WeightVector; terms: Record<string, number>; runner_up: RunnerUp };
export type Explanation = { metrics: Record<string, number>; objective_points: Record<string, number>; won: string[]; lost: string[]; rank_by_objective: Record<string, number> };
export type Schedule = { rank: number; total_points: number; credits: number; courses: string[]; sections: ChosenSection[]; explanation: Explanation; relaxed: string[] };
export type PlanResult = {
  schedules: Schedule[]; feasible: boolean; relaxation: { relaxed: string[]; cost: number; reason: string } | null;
  candidate_courses: string[]; excluded_courses: Record<string, string>; solver_status: string; error?: string;
};
